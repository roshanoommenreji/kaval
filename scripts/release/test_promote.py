from __future__ import annotations

import json
from pathlib import Path

import promote
import pytest

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
