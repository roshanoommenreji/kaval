from __future__ import annotations

import contextlib
import http.server
import json
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import promote
import pytest
from staging_smoke import Check

D = {
    s: "sha256:" + str(i) * 64 for i, s in enumerate(("gateway", "agent", "executor", "collector"))
}
RECORD = json.dumps(
    {
        "schema": 1,
        "passes": [{"tag": "sha-1111111", "commit": "1" * 40, "passed_at": "t", "images": D}],
    }
)


def values(tag: str) -> str:
    return "".join(
        f"{s}:\n  image:\n    tag: {tag}\n" for s in ("gateway", "agent", "executor", "collector")
    )


def files(tag: str) -> dict[str, str | None]:
    return {f: values(tag) for f in promote.PROD_FILES}


# ── the record ────────────────────────────────────────────────────────────────────────


def test_record_has_the_tag() -> None:
    (c,) = promote.check_record(RECORD, "sha-1111111")
    assert c.ok


def test_record_without_the_tag_refuses() -> None:
    (c,) = promote.check_record(RECORD, "sha-2222222")
    assert not c.ok and "never passed staging" in c.detail


def test_empty_or_missing_record_refuses() -> None:
    assert not promote.check_record(None, "sha-1111111")[0].ok
    assert not promote.check_record('{"passes": []}', "sha-1111111")[0].ok


def test_entry_missing_a_service_digest_refuses() -> None:
    thin = json.dumps({"passes": [{"tag": "sha-1111111", "images": {"gateway": D["gateway"]}}]})
    assert not promote.check_record(thin, "sha-1111111")[0].ok


def test_malformed_digest_refuses() -> None:
    bad = json.dumps({"passes": [{"tag": "sha-1111111", "images": {**D, "agent": "latest"}}]})
    assert not promote.check_record(bad, "sha-1111111")[0].ok


# ── ECR ───────────────────────────────────────────────────────────────────────────────


def test_ecr_match_passes() -> None:
    assert promote.check_ecr({"images": D}, dict(D)).ok


def test_ecr_repointed_tag_refuses_and_names_the_service() -> None:
    c = promote.check_ecr({"images": D}, {**D, "executor": "sha256:" + "f" * 64})
    assert not c.ok and "executor" in c.detail and "gateway" not in c.detail


# ── direction ─────────────────────────────────────────────────────────────────────────


def test_direction() -> None:
    assert promote.check_direction("sha-aaaaaaa", "sha-bbbbbbb", is_ancestor=False).ok
    same = promote.check_direction("sha-aaaaaaa", "sha-aaaaaaa", is_ancestor=False)
    assert not same.ok and "already runs" in same.detail
    older = promote.check_direction("sha-bbbbbbb", "sha-aaaaaaa", is_ancestor=True)
    assert not older.ok and "rollback" in older.detail


# ── stories and UAT ───────────────────────────────────────────────────────────────────


def test_release_keys_from_subjects() -> None:
    subjects = (
        "Merge pull request #78 from me/feat/KAV-62-staging-smoke\n"
        "feat(infra): prove it (KAV-62)\n"
        "docs: KAV-9 and KAV-44 mentioned\n"
        "chore: no key here\n"
    )
    assert promote.release_keys(subjects) == ["KAV-9", "KAV-44", "KAV-62"]


def test_uat_story_not_signed_off_refuses() -> None:
    issues = {"KAV-44": {"labels": ["uat"], "status": "In Staging"}}
    c = promote.check_uat(["KAV-44"], issues)
    assert not c.ok and "KAV-44 (In Staging)" in c.detail


@pytest.mark.parametrize("status", ["Ready for Prod", "Done"])
def test_uat_story_signed_off_passes(status: str) -> None:
    assert promote.check_uat(["KAV-44"], {"KAV-44": {"labels": ["uat"], "status": status}}).ok


def test_story_without_uat_label_is_not_blocked_by_its_status() -> None:
    assert promote.check_uat(
        ["KAV-50"], {"KAV-50": {"labels": ["infra"], "status": "In Progress"}}
    ).ok


def test_one_unsigned_story_among_good_ones_refuses() -> None:
    issues = {
        "KAV-40": {"labels": ["uat"], "status": "Done"},
        "KAV-44": {"labels": ["uat"], "status": "In Staging"},
    }
    assert not promote.check_uat(["KAV-40", "KAV-44"], issues).ok


def test_unknown_key_is_noted_not_fatal() -> None:
    c = promote.check_uat(["KAV-9999"], {"KAV-9999": None})
    assert c.ok and "KAV-9999" in c.detail


def test_no_stories_passes() -> None:
    assert promote.check_uat([], {}).ok


# ── the guard ─────────────────────────────────────────────────────────────────────────


def test_guard_unchanged_tags_pass() -> None:
    (c,) = promote.guard(files("sha-0000000"), files("sha-0000000"), RECORD)
    assert c.ok and "unchanged" in c.detail


def test_guard_new_tag_on_the_record_passes() -> None:
    assert all(c.ok for c in promote.guard(files("sha-0000000"), files("sha-1111111"), RECORD))


def test_guard_new_tag_not_on_the_record_refuses() -> None:
    cs = promote.guard(files("sha-0000000"), files("sha-2222222"), RECORD)
    assert not all(c.ok for c in cs)
    assert "not on the record" in cs[-1].detail


def test_guard_record_must_come_from_the_base_not_the_change() -> None:
    # The same tag, but the base had no record entry: a change cannot supply its own proof.
    assert not all(c.ok for c in promote.guard(files("sha-0000000"), files("sha-1111111"), None))


def test_guard_split_release_refuses() -> None:
    head = files("sha-1111111")
    head[promote.PROD_FILES[0]] = values("sha-1111111").replace("sha-1111111", "sha-0000000", 1)
    cs = promote.guard(files("sha-0000000"), head, RECORD)
    assert not cs[0].ok and "one release" in cs[0].name


def test_guard_missing_tag_lines_refuses() -> None:
    head = files("sha-1111111")
    head[promote.PROD_FILES[1]] = "gateway:\n  image:\n    tag: sha-1111111\n"
    assert not promote.guard(files("sha-0000000"), head, RECORD)[0].ok


def test_guard_ignores_a_tag_mentioned_in_a_comment() -> None:
    commented = files("sha-0000000")
    commented[promote.PROD_FILES[1]] = (
        "# the digest tagged `sha-9999999` was built once\n" + values("sha-0000000")
    )
    assert promote.guard(files("sha-0000000"), commented, RECORD)[0].ok


# ── pin and guard, end to end on a scratch repository ─────────────────────────────────


def _repo(tmp_path: Path, tag: str, record: str = RECORD) -> None:
    for f in promote.PROD_FILES:
        p = tmp_path / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(values(tag), encoding="utf-8")
    r = tmp_path / promote.RECORD
    r.parent.mkdir(parents=True, exist_ok=True)
    r.write_text(record, encoding="utf-8")


def test_pin_rewrites_both_prod_files(tmp_path: Path) -> None:
    _repo(tmp_path, "sha-0000000")
    assert promote.main(["--root", str(tmp_path), "pin", "--tag", "sha-1111111"]) == 0
    for f in promote.PROD_FILES:
        text = (tmp_path / f).read_text(encoding="utf-8")
        assert text.count("tag: sha-1111111") == 4 and "0000000" not in text


def test_pin_refuses_a_tag_off_the_record_and_writes_nothing(tmp_path: Path) -> None:
    _repo(tmp_path, "sha-0000000")
    assert promote.main(["--root", str(tmp_path), "pin", "--tag", "sha-2222222"]) == 1
    assert "sha-0000000" in (tmp_path / promote.PROD_FILES[0]).read_text(encoding="utf-8")


def test_pin_refuses_a_malformed_tag(tmp_path: Path) -> None:
    _repo(tmp_path, "sha-0000000")
    assert promote.main(["--root", str(tmp_path), "pin", "--tag", "latest"]) == 1


def test_pin_stops_if_a_file_changed_shape(tmp_path: Path) -> None:
    _repo(tmp_path, "sha-0000000")
    (tmp_path / promote.PROD_FILES[1]).write_text(
        "gateway:\n  image:\n    tag: sha-0000000\n", encoding="utf-8"
    )
    assert promote.main(["--root", str(tmp_path), "pin", "--tag", "sha-1111111"]) == 1
    # Neither file was touched: a half-pinned prod is worse than a refused pin.
    assert "sha-0000000" in (tmp_path / promote.PROD_FILES[0]).read_text(encoding="utf-8")


def test_pr_body_names_the_tag_digests_and_the_meaning_of_merging(tmp_path: Path) -> None:
    _repo(tmp_path, "sha-0000000")
    evidence = tmp_path / "verify.txt"
    evidence.write_text("PASS  tag is on the record", encoding="utf-8")
    entry = json.loads(RECORD)["passes"][0]
    body = promote.pr_body(entry, "sha-0000000", evidence.read_text(encoding="utf-8"))
    assert "`kaval/gateway:sha-1111111`" in body and D["agent"] in body
    assert "PASS  tag is on the record" in body and "go/no-go" in body
    assert "sha-0000000" in body


# ── the pre-flight `make up` runs (KAV-68) ────────────────────────────────────────────


def test_preflight_passes_when_the_images_exist() -> None:
    check = promote.check_pinned_images("sha-1111111", lambda tag: D)
    assert check.ok and "sha-1111111" in check.detail


def test_preflight_fails_and_says_what_to_do_when_ecr_no_longer_has_the_tag() -> None:
    def gone(tag: str) -> dict[str, str]:
        raise RuntimeError(f"kaval/gateway:{tag} is not readable in ECR: not found")

    check = promote.check_pinned_images("sha-1111111", gone)
    assert not check.ok and "promote.yml" in check.detail and "not found" in check.detail


# ── Jira's login never follows a redirect (KAV-123, ADR-0075) ─────────────────────────
# `verify` reads Jira with the real login from Actions secrets. These run against a local web
# server standing in for Jira, with a login built from pieces so it can never pass for a real one.

REDIRECT_STATUSES = [301, 302, 303, 307, 308]  # the answers urllib's own handler would follow
ISSUE = '{"fields": {"labels": ["uat"], "status": {"name": "Done"}}}'
TOKEN = "-".join(["not", "a", "real", "token"])

Answer = Callable[[str], tuple[int, dict[str, str], str]]  # path -> status, headers, body


class Site:
    """A local web server. It records the path and Authorization header of every request and
    answers as `answer(path)` says."""

    def __init__(self, answer: Answer) -> None:
        self.seen: list[tuple[str, str | None]] = []
        site = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                site.seen.append((self.path, self.headers.get("Authorization")))
                status, headers, body = answer(self.path)
                payload = body.encode()
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: object) -> None:
                pass  # keep the test output quiet

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


Start = Callable[[Answer], Site]


@pytest.fixture
def site() -> Iterator[Start]:
    started: list[Site] = []

    def start(answer: Answer) -> Site:
        made = Site(answer)
        started.append(made)
        return made

    yield start
    for made in started:
        made.close()


def point_jira_at(monkeypatch: pytest.MonkeyPatch, base: str) -> None:
    monkeypatch.setenv("JIRA_SITE_URL", base)
    monkeypatch.setenv("JIRA_EMAIL", "someone@example.invalid")
    monkeypatch.setenv("JIRA_API_TOKEN", TOKEN)


@pytest.mark.parametrize("status", REDIRECT_STATUSES)
def test_a_redirected_jira_read_never_takes_the_login_to_another_address(
    site: Start, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    elsewhere = site(lambda path: (200, {}, ISSUE))
    jira = site(lambda path: (status, {"Location": f"{elsewhere.base}{path}"}, ""))
    point_jira_at(monkeypatch, jira.base)
    with contextlib.suppress(Exception):  # how the read ends is the next test's business
        promote.jira_issue("KAV-1")
    time.sleep(0.2)  # a moment for a request that must not come
    assert elsewhere.seen == []  # not the login, and not even a request
    assert len(jira.seen) == 1


def test_a_redirected_jira_read_stops_with_a_plain_message(
    site: Start, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = "-".join(["signed", "link", "marker"])  # stands for a token in a redirect's address
    elsewhere = site(lambda path: (200, {}, ISSUE))
    jira = site(lambda path: (302, {"Location": f"{elsewhere.base}/{marker}"}, ""))
    point_jira_at(monkeypatch, jira.base)
    with pytest.raises(RuntimeError) as caught:
        promote.jira_issue("KAV-1")
    message = str(caught.value)
    assert message.startswith(
        "Jira answered the read of KAV-1 with a redirect (HTTP 302). It was not followed, "
        "so the login stays on this site."
    )
    for hidden in (jira.base, elsewhere.base, marker, TOKEN):
        assert hidden not in message
    assert caught.value.__cause__ is None  # nothing about the redirect is chained on
    assert len(jira.seen) == 1


def test_a_jira_read_still_returns_labels_and_status_and_sends_the_login(
    site: Start, monkeypatch: pytest.MonkeyPatch
) -> None:
    jira = site(lambda path: (200, {}, ISSUE))
    point_jira_at(monkeypatch, jira.base)
    assert promote.jira_issue("KAV-1") == {"labels": ["uat"], "status": "Done"}
    path, authorization = jira.seen[0]
    assert path == "/rest/api/3/issue/KAV-1?fields=labels,status"
    assert authorization is not None and authorization.startswith("Basic ")


def test_an_unknown_jira_issue_is_none(site: Start, monkeypatch: pytest.MonkeyPatch) -> None:
    jira = site(lambda path: (404, {}, "{}"))
    point_jira_at(monkeypatch, jira.base)
    assert promote.jira_issue("KAV-9999") is None


def test_any_other_jira_error_stops_the_read(site: Start, monkeypatch: pytest.MonkeyPatch) -> None:
    jira = site(lambda path: (500, {}, "{}"))
    point_jira_at(monkeypatch, jira.base)
    with pytest.raises(RuntimeError, match="Jira answered 500 for KAV-1"):
        promote.jira_issue("KAV-1")


# ── a promotion is a tagged release (KAV-135) ─────────────────────────────────────────


def release_check(
    tags: list[str], code: str = "0.2.0", prod: str = "0.1.0", on_main: bool = True
) -> Check:
    return promote.check_release(tags, code, prod, on_main)


def test_a_new_tagged_version_on_main_may_go() -> None:
    c = release_check(["v0.2.0"])
    assert c.ok and "v0.2.0" in c.detail and "v0.1.0" in c.detail


def test_an_untagged_build_is_refused_and_told_what_to_do() -> None:
    c = release_check([])
    assert not c.ok and "no version tag" in c.detail and "release-prepare" in c.detail


def test_a_tag_that_disagrees_with_the_code_is_refused() -> None:
    c = release_check(["v9.9.9"])
    assert not c.ok and "v9.9.9" in c.detail and "0.2.0" in c.detail


def test_the_version_prod_already_runs_is_refused() -> None:
    c = release_check(["v0.1.0"], code="0.1.0", prod="0.1.0")
    assert not c.ok and "already runs" in c.detail


def test_an_older_version_is_refused() -> None:
    c = release_check(["v0.1.9"], code="0.1.9", prod="0.2.0")
    assert not c.ok and "newer" in c.detail


def test_versions_compare_as_numbers_not_text() -> None:
    assert release_check(["v0.10.0"], code="0.10.0", prod="0.9.0").ok


def test_a_commit_off_main_is_refused() -> None:
    c = release_check(["v0.2.0"], on_main=False)
    assert not c.ok and "main" in c.detail


def test_release_facts_read_only_annotated_version_tags_on_the_commit(tmp_path: Path) -> None:
    import subprocess

    def git(*a: str) -> str:
        return subprocess.run(
            ["git", *a], cwd=tmp_path, capture_output=True, text=True, check=True
        ).stdout.strip()

    git("init", "-q", "-b", "main")
    for k, v in (("user.email", "t@example.com"), ("user.name", "t"), ("commit.gpgsign", "false")):
        git("config", k, v)
    git("config", "tag.gpgsign", "false")
    (tmp_path / "f").write_text("x", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "feat: x")
    git("tag", "-a", "v0.2.0", "-m", "Kaval 0.2.0")
    git("tag", "v0.3.0")  # lightweight: anyone can push one, so it does not count
    git("tag", "gateway-v0.2.0", "-m", "x")  # a component tag is not the product's
    commit = git("rev-parse", "HEAD")
    assert promote.release_tags_at(commit, tmp_path) == ["v0.2.0"]
    assert promote.is_on_main(commit, tmp_path)


# ── promote by release: v0.4.2 becomes its image tag (KAV-136) ────────────────────────

COMMIT = "791c44d" + "0" * 33


def _ecr_has(*tags: str) -> Callable[[str], dict[str, str]]:
    def lookup(tag: str) -> dict[str, str]:
        if tag not in tags:
            raise RuntimeError(f"kaval/gateway:{tag} is not readable in ECR")
        return D

    return lookup


def test_a_release_becomes_the_image_tag_of_its_commit() -> None:
    tag, note = promote.resolve_ref(
        "v0.4.2", lambda v: COMMIT if v == "v0.4.2" else None, _ecr_has("sha-791c44d")
    )
    assert tag == "sha-791c44d"
    assert "v0.4.2" in note and "sha-791c44d" in note  # the evidence names both


def test_an_image_tag_passes_through_as_before_without_asking_git_or_the_registry() -> None:
    def never(_: str) -> None:
        raise AssertionError("must not be asked")

    tag, note = promote.resolve_ref("sha-2459417", never, never)  # type: ignore[arg-type]
    assert tag == "sha-2459417" and "sha-2459417" in note


def test_an_unknown_release_is_refused_with_a_plain_message() -> None:
    with pytest.raises(ValueError, match=r"v9\.9\.9 is not a release"):
        promote.resolve_ref("v9.9.9", lambda v: None, _ecr_has())


def test_a_release_whose_commit_has_no_image_is_refused_and_says_so() -> None:
    with pytest.raises(ValueError, match=r"no image tagged sha-791c44d.*Nothing was opened"):
        promote.resolve_ref("v0.4.2", lambda v: COMMIT, _ecr_has("sha-other00"))


@pytest.mark.parametrize(
    "typed",
    [
        "",
        "0.4.2",
        "v0.4",
        "v0.4.2.1",
        "latest",
        "main",
        "v0.4.2; rm -rf /",
        "sha-xyz",
        "SHA-2459417",
    ],
)
def test_anything_else_is_refused_before_git_or_the_registry_is_asked(typed: str) -> None:
    def never(_: str) -> None:
        raise AssertionError("must not be asked")

    with pytest.raises(ValueError, match="neither a release version nor an image tag"):
        promote.resolve_ref(typed, never, never)  # type: ignore[arg-type]


def test_a_person_may_type_a_space_around_it() -> None:
    tag, _ = promote.resolve_ref(" v0.4.2 ", lambda v: COMMIT, _ecr_has("sha-791c44d"))
    assert tag == "sha-791c44d"


def test_the_command_prints_only_the_tag_on_stdout_and_writes_the_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(promote, "commit_of_version", lambda v, root=promote.ROOT: COMMIT)
    monkeypatch.setattr(promote, "ecr_digests", _ecr_has("sha-791c44d"))
    note = tmp_path / "resolve.txt"
    rc = promote.main(["resolve", "--ref", "v0.4.2", "--note", str(note)])
    out = capsys.readouterr()
    assert rc == 0 and out.out == "sha-791c44d\n"  # a step output: nothing else may be on stdout
    assert "v0.4.2" in note.read_text(encoding="utf-8")


def test_a_refusal_exits_1_prints_nothing_on_stdout_and_writes_no_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(promote, "commit_of_version", lambda v, root=promote.ROOT: None)
    note = tmp_path / "resolve.txt"
    rc = promote.main(["resolve", "--ref", "v9.9.9", "--note", str(note)])
    out = capsys.readouterr()
    assert rc == 1 and out.out == "" and "refused:" in out.err and not note.exists()


def test_commit_of_version_accepts_only_an_annotated_release_tag(tmp_path: Path) -> None:
    import subprocess

    def git(*a: str) -> str:
        return subprocess.run(
            ["git", *a], cwd=tmp_path, capture_output=True, text=True, check=True
        ).stdout.strip()

    git("init", "-q", "-b", "main")
    for k, v in (("user.email", "t@example.com"), ("user.name", "t"), ("commit.gpgsign", "false")):
        git("config", k, v)
    git("config", "tag.gpgsign", "false")
    (tmp_path / "f").write_text("x", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "feat: x")
    git("tag", "-a", "v0.2.0", "-m", "Kaval 0.2.0")
    git("tag", "v0.3.0")  # lightweight: anyone can push one, so it is not a release
    head = git("rev-parse", "HEAD")
    assert promote.commit_of_version("v0.2.0", tmp_path) == head  # peeled to the commit
    assert promote.commit_of_version("v0.3.0", tmp_path) is None
    assert promote.commit_of_version("v9.9.9", tmp_path) is None


def test_the_pull_request_text_names_the_release_beside_the_image_tag() -> None:
    entry = json.loads(RECORD)["passes"][0]
    named = promote.pr_body(entry, "sha-0000000", "ok", "0.4.2")
    assert "release v0.4.2" in named and "`sha-1111111`" in named
    assert "release v" not in promote.pr_body(entry, "sha-0000000", "ok")  # old callers unchanged


def test_the_workflow_hands_the_resolved_tag_on_and_keeps_input_out_of_scripts() -> None:
    text = (promote.ROOT / ".github/workflows/promote.yml").read_text(encoding="utf-8")
    assert "steps.resolve.outputs.tag" in text and "needs.gate.outputs.tag" in text
    # the typed text reaches a script only through the environment, never `run:` interpolation
    assert "${{ inputs.tag }}" in text
    for line in text.splitlines():
        if "${{ inputs.tag }}" in line:
            assert line.strip().startswith("REF:"), line
