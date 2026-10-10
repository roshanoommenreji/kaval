from __future__ import annotations

import json
import subprocess
from pathlib import Path

import promote
import pytest
import rollback

D = {
    s: "sha256:" + str(i) * 64 for i, s in enumerate(("gateway", "agent", "executor", "collector"))
}
ENTRY = {"tag": "sha-1111111", "commit": "1" * 40, "passed_at": "t", "images": D}
RECORD = json.dumps({"schema": 1, "passes": [ENTRY]})
NONE: frozenset[str] = frozenset()


def values(tag: str) -> str:
    return "".join(
        f"{s}:\n  image:\n    tag: {tag}\n" for s in ("gateway", "agent", "executor", "collector")
    )


def files(tag: str) -> dict[str, str | None]:
    return {f: values(tag) for f in promote.PROD_FILES}


# ── is the target a version we already trusted? ───────────────────────────────────────


def test_known_when_prod_ran_it_before_even_if_not_on_the_record() -> None:
    c = rollback.check_known(RECORD, frozenset({"sha-0000000"}), "sha-0000000")
    assert c.ok and "prod has run" in c.detail


def test_known_when_it_passed_staging_but_prod_never_ran_it() -> None:
    c = rollback.check_known(RECORD, NONE, "sha-1111111")
    assert c.ok and "passed staging" in c.detail


def test_unknown_tag_refuses() -> None:
    c = rollback.check_known(RECORD, frozenset({"sha-0000000"}), "sha-2222222")
    assert not c.ok and "never ran in prod" in c.detail


def test_known_with_no_record_at_all() -> None:
    assert rollback.check_known(None, frozenset({"sha-0000000"}), "sha-0000000").ok
    assert not rollback.check_known(None, NONE, "sha-0000000").ok


# ── direction ─────────────────────────────────────────────────────────────────────────


def test_earlier_passes_when_target_is_an_ancestor() -> None:
    assert rollback.check_earlier("sha-2222222", "sha-1111111", True).ok


def test_same_tag_is_nothing_to_do() -> None:
    c = rollback.check_earlier("sha-1111111", "sha-1111111", True)
    assert not c.ok and "already runs" in c.detail


def test_newer_or_unrelated_tag_belongs_to_promote() -> None:
    c = rollback.check_earlier("sha-1111111", "sha-2222222", False)
    assert not c.ok and "promote.yml" in c.detail


# ── the images ────────────────────────────────────────────────────────────────────────


def test_images_on_the_record_must_match_the_recorded_digests() -> None:
    assert rollback.check_images(ENTRY, dict(D)).ok
    changed = {**D, "agent": "sha256:" + "f" * 64}
    c = rollback.check_images(ENTRY, changed)
    assert not c.ok and "agent" in c.detail


def test_images_older_than_the_record_only_need_to_exist() -> None:
    c = rollback.check_images(None, dict(D))
    assert c.ok and "no digest to compare" in c.detail


def test_a_missing_service_image_refuses() -> None:
    short = {k: v for k, v in D.items() if k != "executor"}
    assert not rollback.check_images(ENTRY, short).ok


# ── the database ──────────────────────────────────────────────────────────────────────


def test_no_migration_between_passes() -> None:
    assert rollback.check_schema([], False).ok


def test_a_migration_refuses_unless_accepted() -> None:
    added = ["migrations/versions/a1c4f9b0e3d2_action_slack_notified_at.py"]
    c = rollback.check_schema(added, False)
    assert not c.ok and "a1c4f9b0e3d2" in c.detail and "accept_migrations" in c.detail
    assert "rollback-prod.md" in c.detail
    assert rollback.check_schema(added, True).ok


# ── the guard learns the same targets ─────────────────────────────────────────────────


def test_guard_allows_a_tag_prod_ran_before() -> None:
    cs = promote.guard(
        files("sha-2222222"), files("sha-0000000"), RECORD, frozenset({"sha-0000000"})
    )
    assert all(c.ok for c in cs) and "rollback" in cs[-1].detail


def test_guard_still_refuses_a_tag_that_is_neither_recorded_nor_run_before() -> None:
    cs = promote.guard(
        files("sha-2222222"), files("sha-3333333"), RECORD, frozenset({"sha-0000000"})
    )
    assert not all(c.ok for c in cs) and "never run it" in cs[-1].detail


def test_guard_without_history_behaves_as_before() -> None:
    assert not all(c.ok for c in promote.guard(files("sha-2222222"), files("sha-0000000"), RECORD))


def test_guard_and_rollback_agree_on_what_is_known() -> None:
    for tag, history in (
        ("sha-1111111", NONE),
        ("sha-0000000", frozenset({"sha-0000000"})),
        ("sha-4444444", NONE),
    ):
        guard_ok = all(
            c.ok for c in promote.guard(files("sha-2222222"), files(tag), RECORD, history)
        )
        assert guard_ok == rollback.check_known(RECORD, history, tag).ok


# ── Git, on a scratch repository ──────────────────────────────────────────────────────


def _git(root: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=root, capture_output=True, text=True, encoding="utf-8", check=True,
    )  # fmt: skip
    return out.stdout.strip()


def _commit_pin(root: Path, tag: str, extra: dict[str, str] | None = None) -> str:
    for f in promote.PROD_FILES:
        p = root / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(values(tag), encoding="utf-8")
    for name, text in (extra or {}).items():
        q = root / name
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_text(text, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", f"pin {tag}")
    return _git(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    return tmp_path


def test_history_lists_every_tag_prod_has_pinned(repo: Path) -> None:
    _commit_pin(repo, "sha-0000000")
    _commit_pin(repo, "sha-1111111")
    _commit_pin(repo, "sha-2222222")
    assert promote.prod_history_tags("HEAD", repo) == {"sha-0000000", "sha-1111111", "sha-2222222"}


def test_history_is_read_from_the_commit_asked_for(repo: Path) -> None:
    first = _commit_pin(repo, "sha-0000000")
    _commit_pin(repo, "sha-1111111")
    assert promote.prod_history_tags(first, repo) == {"sha-0000000"}


def test_a_tag_only_on_a_branch_is_not_history(repo: Path) -> None:
    _commit_pin(repo, "sha-0000000")
    _git(repo, "switch", "-q", "-c", "side")
    _commit_pin(repo, "sha-9999999")
    _git(repo, "switch", "-q", "-")
    assert promote.prod_history_tags("HEAD", repo) == {"sha-0000000"}


def test_migrations_added_between_two_commits(repo: Path) -> None:
    old = _commit_pin(repo, "sha-0000000", {"migrations/versions/aaa_first.py": "x = 1\n"})
    new = _commit_pin(repo, "sha-1111111", {"migrations/versions/bbb_second.py": "x = 2\n"})
    assert rollback.migrations_added(old, new, repo) == ["migrations/versions/bbb_second.py"]
    assert rollback.migrations_added(new, new, repo) == []


def test_ancestor_direction(repo: Path) -> None:
    old = _commit_pin(repo, "sha-0000000")
    new = _commit_pin(repo, "sha-1111111")
    assert rollback.is_ancestor(old, new, repo)
    assert not rollback.is_ancestor(new, old, repo)


# ── pin and the pull request text ─────────────────────────────────────────────────────


def _pin_repo(repo: Path) -> None:
    _commit_pin(repo, "sha-0000000")
    _commit_pin(repo, "sha-2222222")
    record = repo / promote.RECORD
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(RECORD, encoding="utf-8")


def test_pin_goes_back_to_a_tag_prod_ran_before(repo: Path) -> None:
    _pin_repo(repo)
    assert rollback.main(["--root", str(repo), "pin", "--tag", "sha-0000000"]) == 0
    for f in promote.PROD_FILES:
        text = (repo / f).read_text(encoding="utf-8")
        assert text.count("tag: sha-0000000") == 4 and "2222222" not in text


def test_pin_refuses_an_unknown_tag_and_writes_nothing(repo: Path) -> None:
    _pin_repo(repo)
    assert rollback.main(["--root", str(repo), "pin", "--tag", "sha-5555555"]) == 1
    assert "sha-2222222" in (repo / promote.PROD_FILES[0]).read_text(encoding="utf-8")


def test_pin_refuses_a_malformed_tag(repo: Path) -> None:
    _pin_repo(repo)
    assert rollback.main(["--root", str(repo), "pin", "--tag", "latest"]) == 1


def test_a_rollback_pin_passes_the_guard(repo: Path) -> None:
    """What rollback.py writes is exactly what promotion-guard lets through."""
    _pin_repo(repo)
    base = {f: (repo / f).read_text(encoding="utf-8") for f in promote.PROD_FILES}
    rollback.main(["--root", str(repo), "pin", "--tag", "sha-0000000"])
    head = {f: (repo / f).read_text(encoding="utf-8") for f in promote.PROD_FILES}
    history = promote.prod_history_tags("HEAD", repo)
    assert all(c.ok for c in promote.guard(base, head, RECORD, history))


# ── the change record of the release being undone ─────────────────────────────────────


def record_text(tag: str, rolled: str = "no") -> str:
    return (
        "# Release v0.1.0 — 2026-10-09\n\n"
        f"- **Environment:** prod\n- **Image tag:** {tag}\n- **Rolled back:** {rolled}\n"
        "- **Time to restore:** —\n"
    )


def _add_record(repo: Path, name: str, text: str) -> Path:
    p = repo / rollback.RELEASES / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_mark_says_where_it_went_when_and_why() -> None:
    out = rollback.mark_rolled_back(record_text("sha-2222222"), "sha-0000000", "2026-10-11", "boom")
    assert out is not None
    assert "- **Rolled back:** yes, to `sha-0000000` on 2026-10-11. Why: boom\n" in out
    assert "- **Time to restore:** —" in out  # other fields untouched


def test_mark_leaves_an_earlier_rollback_alone() -> None:
    done = record_text("sha-2222222", "yes, to `sha-0000000` on 2026-10-11")
    assert rollback.mark_rolled_back(done, "sha-1111111", "2026-10-12", "") is None


def test_mark_flattens_and_cuts_the_reason() -> None:
    out = rollback.mark_rolled_back(record_text("t"), "sha-0000000", "d", "a\nb\n" + "Q" * 5000)
    assert out is not None and "a b Q" in out and out.count("Q") <= rollback.REASON_MAX


def test_a_record_without_the_field_is_left_alone() -> None:
    assert rollback.mark_rolled_back("# Release\n", "sha-0000000", "d", "x") is None


def test_find_record_picks_the_newest_file_for_that_tag(tmp_path: Path) -> None:
    _add_record(tmp_path, "2026-10-01-v0.1.0.md", record_text("sha-2222222"))
    newer = _add_record(tmp_path, "2026-10-05-v0.2.0.md", record_text("sha-2222222"))
    _add_record(tmp_path, "2026-10-06-v0.3.0.md", record_text("sha-9999999"))
    _add_record(tmp_path, "README.md", record_text("sha-2222222"))  # not date-prefixed
    releases = tmp_path / rollback.RELEASES
    assert rollback.find_record(releases, "sha-2222222") == newer
    assert rollback.find_record(releases, "sha-5555555") is None


def test_pin_marks_the_record_of_the_release_it_undoes(repo: Path) -> None:
    _pin_repo(repo)
    undone = _add_record(repo, "2026-10-09-v0.1.0.md", record_text("sha-2222222"))
    argv = ["--root", str(repo), "pin", "--tag", "sha-0000000", "--reason", "crash loop"]
    assert rollback.main([*argv, "--date", "2026-10-11"]) == 0
    assert "yes, to `sha-0000000` on 2026-10-11. Why: crash loop" in undone.read_text("utf-8")


def test_pin_still_succeeds_when_there_is_no_record(repo: Path) -> None:
    _pin_repo(repo)
    assert rollback.main(["--root", str(repo), "pin", "--tag", "sha-0000000"]) == 0
    assert "tag: sha-0000000" in (repo / promote.PROD_FILES[0]).read_text(encoding="utf-8")


def test_a_refused_pin_marks_nothing(repo: Path) -> None:
    _pin_repo(repo)
    undone = _add_record(repo, "2026-10-09-v0.1.0.md", record_text("sha-2222222"))
    assert rollback.main(["--root", str(repo), "pin", "--tag", "sha-5555555"]) == 1
    assert undone.read_text("utf-8") == record_text("sha-2222222")


def test_the_dashboard_counts_a_marked_record_as_rolled_back(tmp_path: Path) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "dashboard", Path(__file__).resolve().parents[1] / "tracking" / "dashboard.py"
    )
    assert spec and spec.loader
    dash = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dash)
    marked = rollback.mark_rolled_back(record_text("sha-2222222"), "sha-0000000", "d", "x")
    assert marked is not None
    _add_record(tmp_path, "2026-10-09-v0.1.0.md", marked)
    assert dash.read_releases(tmp_path / rollback.RELEASES)[0]["rolled_back"] is True


def test_pr_body_says_why_what_moves_and_what_it_does_not_undo() -> None:
    body = rollback.pr_body(
        "sha-1111111", "sha-2222222", "agent crash-loops after the new image", "PASS  ok", ENTRY
    )
    assert "`sha-2222222` to `sha-1111111`" in body and "crash-loops" in body
    assert "`kaval/gateway:sha-1111111`" in body and "go/no-go" in body
    assert "does not undo database changes" in body


def test_pr_body_for_a_tag_older_than_the_record_has_no_digest_table() -> None:
    body = rollback.pr_body("sha-0000000", "sha-2222222", "", "PASS  ok", None)
    assert "digest that passed" not in body and "(no reason given)" in body


def test_pr_body_cuts_a_very_long_reason() -> None:
    body = rollback.pr_body("sha-0000000", "sha-2222222", "Q" * 5000, "ok", None)
    assert body.count("Q") == rollback.REASON_MAX


def test_committed_prod_tag_ignores_a_pin_not_yet_committed(repo: Path) -> None:
    """promote.yml pins first and writes the pull request text second; the text must still say what
    prod runs *before* the change (pull request #83's text named the new tag instead)."""
    _commit_pin(repo, "sha-0000000")
    for f in promote.PROD_FILES:
        (repo / f).write_text(values("sha-1111111"), encoding="utf-8")
    assert promote.current_prod_tag(repo) == "sha-1111111"
    assert promote.committed_prod_tag(repo) == "sha-0000000"
