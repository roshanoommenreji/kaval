from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import change_record as cr
import pytest
import versions
from versions import Commit

ROOT = Path(__file__).resolve().parents[2]
D = {
    s: "sha256:" + str(i) * 64 for i, s in enumerate(("gateway", "agent", "executor", "collector"))
}
ENTRY = {
    "tag": "sha-2222222",
    "commit": "2" * 40,
    "passed_at": "2026-10-08T17:27:51Z",
    "images": D,
    "published_not_exercised": {"backup": "sha256:" + "b" * 64},
    "checks": [{"name": "pods running and ready", "detail": "4 running"}],
    "not_covered": ["the backup image: published, not deployed to staging"],
}
EVIDENCE = (
    "  PASS  every uat story in the release is signed off: 3 stories, 1 with uat, all accepted\n"
    "sha-2222222 may be promoted to prod.\n"
)
OLD = dict.fromkeys(versions.COMPONENTS, "0.1.0")


def commit(subject: str, *files: str, body: str = "") -> Commit:
    return Commit("c" * 40, subject, body, files)


def inputs(**over: object) -> cr.Inputs:
    base = {
        "entry": ENTRY,
        "prev_tag": "sha-1111111",
        "prev_entry": None,
        "new": ({**OLD, "gateway": "0.2.0"}, "0.2.0"),
        "prev": (OLD, "0.1.0"),
        "commits": [
            commit("feat(gateway): a thing (KAV-12)", "services/gateway/kaval_gateway/a.py"),
            commit("docs: words (KAV-3)", "docs/x.md"),
        ],
        "migrations": [],
        "unreleased": "none; the images carry exactly the tagged versions",
        "lead_seconds": 3.1 * 86400,
        "evidence": EVIDENCE,
        "actor": "roshan",
        "date": "2026-10-11",
    }
    return cr.Inputs(**{**base, **over})  # type: ignore[arg-type]


# ── risk is derived from what the diff touches ────────────────────────────────────────


def test_a_docs_only_release_is_low_risk() -> None:
    assert cr.risk([commit("docs: x", "docs/a.md")])[0] == "low"


def test_a_migration_makes_it_high_and_says_why() -> None:
    level, why = cr.risk([commit("feat: x", "migrations/versions/abc_x.py")])
    assert level == "high" and "database migration" in why[0]


@pytest.mark.parametrize(
    ("path", "level"),
    [
        ("services/executor/kaval_executor/x.py", "high"),
        ("policy/actions.rego", "high"),
        ("infra/modules/node/main.tf", "medium"),
        ("deploy/charts/kaval/values.yaml", "medium"),
        ("services/shared/kaval_shared/m.py", "medium"),
        ("uv.lock", "medium"),
        ("services/gateway/kaval_gateway/a.py", "low"),
    ],
)
def test_risk_levels(path: str, level: str) -> None:
    assert cr.risk([commit("fix: x", path)])[0] == level


def test_a_breaking_change_is_high_risk() -> None:
    assert cr.risk([commit("feat!: x", "services/gateway/a.py")])[0] == "high"


def test_keys_are_sorted_numerically_and_unique() -> None:
    cs = [commit("fix: a (KAV-10)"), commit("fix: b (KAV-9) (KAV-10)")]
    assert cr.jira_keys(cs) == ["KAV-9", "KAV-10"]


def test_uat_comes_from_the_gates_output() -> None:
    assert cr.uat_line(EVIDENCE).startswith("3 stories")
    assert "not recorded" in cr.uat_line("")


def test_lead_time_reads_in_days_or_hours() -> None:
    assert cr.lead_time(3.1 * 86400) == "3.1 days"
    assert cr.lead_time(5400) == "1.5 hours"
    assert cr.lead_time(None) == "—"


def test_a_second_record_for_the_same_version_and_day_gets_a_suffix() -> None:
    assert cr.file_name("2026-10-11", "0.2.0", set()) == "2026-10-11-v0.2.0.md"
    assert cr.file_name("2026-10-11", "0.2.0", {"2026-10-11-v0.2.0.md"}) == "2026-10-11-v0.2.0-2.md"


# ── the document ──────────────────────────────────────────────────────────────────────


def test_the_record_has_every_section_the_readme_promises() -> None:
    text = cr.render(inputs())
    for section in (
        "## Components", "## Changes", "## Issues", "## Risk",
        "## Staging evidence", "## Rollback plan", "## Verification",
    ):  # fmt: skip
        assert section in text
    assert text.startswith("# Release v0.2.0 — 2026-10-11")
    assert "Kaval 0.2.0: gateway 0.2.0 · collector 0.1.0" in text


def test_the_dashboard_can_read_what_was_generated(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location(
        "dashboard", ROOT / "scripts/tracking/dashboard.py"
    )
    assert spec and spec.loader
    dashboard = importlib.util.module_from_spec(spec)
    sys.modules["dashboard"] = dashboard  # dataclasses look their own module up by name
    spec.loader.exec_module(dashboard)
    (tmp_path / "2026-10-11-v0.2.0.md").write_text(cr.render(inputs()), encoding="utf-8")
    got = dashboard.read_releases(tmp_path)
    assert got and got[0]["version"] == "v0.2.0" and got[0]["date"] == "2026-10-11"
    assert got[0]["env"] == "prod" and got[0]["approver"] == "roshan"
    assert got[0]["lead_time"] == "3.1 days" and got[0]["rolled_back"] is False
    assert got[0]["digest"] == D["gateway"][:14]


def test_components_show_what_moved_and_what_did_not() -> None:
    rows = cr.component_rows(inputs())
    assert "| gateway | 0.2.0 | 0.1.0 |" in rows
    assert "| backup | 0.1.0 | 0.1.0 | sha256:" + "b" * 64 in rows


def test_an_image_whose_digest_equals_the_previous_releases_is_marked_unchanged() -> None:
    prev = {"images": {"agent": D["agent"]}}
    assert "(unchanged)" in cr.component_rows(inputs(prev_entry=prev)).split("| agent |")[1]


def test_changes_are_grouped_by_component_with_the_rest_apart() -> None:
    text = cr.changes(inputs().commits)
    assert text.index("**gateway**") < text.index("**no component")
    assert "feat(gateway): a thing (KAV-12)" in text


def test_the_rollback_plan_names_the_version_to_go_back_to_and_the_command() -> None:
    plan = cr.rollback_plan(inputs())
    assert "-f tag=sha-1111111" in plan and "crosses no database migration" in plan
    assert "80 seconds" in plan


def test_a_release_with_a_migration_warns_in_the_rollback_plan() -> None:
    plan = cr.rollback_plan(inputs(migrations=["migrations/versions/a1c4_x.py"]))
    assert "crosses a database migration" in plan and "accept_migrations" in plan


def test_verification_is_honest_that_nothing_has_run_yet() -> None:
    assert "Not recorded yet" in cr.render(inputs())


def test_a_release_with_no_commits_says_so() -> None:
    assert "No commits" in cr.changes([])


def test_rendering_for_the_tag_prod_already_pins_is_refused(tmp_path: Path) -> None:
    (tmp_path / "deploy/promotion").mkdir(parents=True)
    (tmp_path / "deploy/promotion/passed-staging.json").write_text(
        json.dumps({"schema": 1, "passes": [ENTRY]}), encoding="utf-8"
    )
    for f in ("deploy/gitops/prod/helmrelease.yaml", "deploy/environments/prod/values.yaml"):
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("".join(f"{s}:\n  tag: sha-2222222\n" for s in D), "utf-8")
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "pin"]):
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *cmd], cwd=tmp_path, check=True
        )
    assert cr.main(["--root", str(tmp_path), "render", "--tag", "sha-2222222"]) == 1


def test_a_note_appears_under_the_heading_and_is_absent_otherwise() -> None:
    assert "*Backfilled after the fact.*" in cr.render(inputs(note="Backfilled after the fact."))
    assert "Backfilled" not in cr.render(inputs())
