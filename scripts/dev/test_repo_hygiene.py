"""The public-repo safety files must exist and say what the decision says (ADR-0071, KAV-109).

  * SECURITY.md tells a stranger how to report privately, and what is out of bounds
  * CODEOWNERS only names paths that exist, and has a default owner
  * the Scorecard workflow stays read-only, pinned and local to the repository's Security tab
  * every third-party action in every workflow is pinned to a full commit SHA (the claim
    SECURITY.md makes about the repository)
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
GITHUB = ROOT / ".github"
WORKFLOWS = GITHUB / "workflows"
SHA_PIN = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def _pinned(uses: str) -> bool:
    """A local action (./...) needs no pin; a published one must name a full commit SHA."""
    return uses.startswith("./") or bool(SHA_PIN.match(uses))


def _uses(workflow: dict) -> list[str]:
    return [
        step["uses"]
        for job in workflow.get("jobs", {}).values()
        for step in job.get("steps", [])
        if "uses" in step
    ]


def _scorecard() -> dict:
    return yaml.safe_load((WORKFLOWS / "scorecard.yml").read_text(encoding="utf-8"))


# --- SECURITY.md ---------------------------------------------------------------------------


def test_the_security_policy_says_how_to_report_privately() -> None:
    text = (GITHUB / "SECURITY.md").read_text(encoding="utf-8")
    assert "advisories/new" in text, "the private report link is missing"
    assert "Report a vulnerability" in text
    assert "public issue" in text, "it must say not to report in public"


def test_the_security_policy_keeps_people_away_from_the_running_systems() -> None:
    text = (GITHUB / "SECURITY.md").read_text(encoding="utf-8")
    assert "What is out of scope" in text
    assert "Do not test against my running systems" in text


def test_the_security_policy_names_no_account_or_address() -> None:
    text = (GITHUB / "SECURITY.md").read_text(encoding="utf-8")
    assert not re.search(r"\b\d{12}\b", text), "an account number"
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text), "an e-mail address"


# --- CODEOWNERS ----------------------------------------------------------------------------


def _owner_lines() -> list[tuple[str, list[str]]]:
    rows = []
    for raw in (GITHUB / "CODEOWNERS").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            pattern, *owners = line.split()
            rows.append((pattern, owners))
    return rows


def test_codeowners_has_a_default_owner() -> None:
    rows = dict(_owner_lines())
    assert rows.get("*"), "no catch-all line: some paths would have no owner"


def test_every_codeowners_line_has_an_owner_that_looks_like_one() -> None:
    for pattern, owners in _owner_lines():
        assert owners, f"{pattern} has no owner"
        assert all(o.startswith("@") for o in owners), f"{pattern}: {owners}"


def test_codeowners_names_only_paths_that_exist() -> None:
    for pattern, _ in _owner_lines():
        if pattern == "*":
            continue
        assert (ROOT / pattern.strip("/")).exists(), f"{pattern} is not in the repository"


def test_codeowners_says_plainly_it_does_not_block_merges() -> None:
    text = (GITHUB / "CODEOWNERS").read_text(encoding="utf-8")
    assert "OFF HERE ON PURPOSE" in text


# --- the Scorecard workflow ----------------------------------------------------------------


def test_the_scorecard_workflow_runs_weekly_and_by_hand() -> None:
    triggers = _scorecard()[True]  # PyYAML reads the key `on` as the boolean True
    assert "schedule" in triggers and "workflow_dispatch" in triggers
    assert "pull_request" not in triggers and "push" not in triggers


def test_the_scorecard_workflow_can_only_read_and_write_the_security_tab() -> None:
    wf = _scorecard()
    assert wf["permissions"] == {}, "the token must start with nothing"
    granted = wf["jobs"]["scorecard"]["permissions"]
    assert granted == {"contents": "read", "actions": "read", "security-events": "write"}


def test_the_scorecard_workflow_publishes_nothing_outside_the_repository() -> None:
    steps = _scorecard()["jobs"]["scorecard"]["steps"]
    card = next(s for s in steps if s["uses"].startswith("ossf/scorecard-action@"))
    assert card["with"]["publish_results"] is False


def test_the_scorecard_checkout_keeps_no_credentials() -> None:
    steps = _scorecard()["jobs"]["scorecard"]["steps"]
    checkout = next(s for s in steps if s["uses"].startswith("actions/checkout@"))
    assert checkout["with"]["persist-credentials"] is False


# --- pins ----------------------------------------------------------------------------------


def test_every_published_action_in_every_workflow_is_pinned_to_a_commit() -> None:
    for path in sorted(WORKFLOWS.glob("*.yml")):
        wf = yaml.safe_load(path.read_text(encoding="utf-8"))
        for uses in _uses(wf):
            assert _pinned(uses), f"{path.name}: {uses} is not pinned to a full commit SHA"


def test_the_pin_check_refuses_a_tag_and_a_branch() -> None:
    assert not _pinned("actions/checkout@v4")
    assert not _pinned("actions/checkout@main")
    assert not _pinned("actions/checkout@3d3c42e")  # a short SHA can be claimed by another commit
    assert _pinned("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1")
    assert _pinned("./.github/actions/local")
