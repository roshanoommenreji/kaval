"""The AWS account number must never be printed in a public Actions log.

(KAV-141.) This repository is public, so its Actions logs are public. The live proof of KAV-138
(13 November 2026) showed the account number in plain text in every release run: the role address
was a repository *variable* (GitHub prints a step's inputs in its header, and only secrets are
hidden from the first line), and the login action hides the account number from later lines only
when asked (`mask-aws-account-id`, off by default).

These tests keep both halves in place for every workflow that logs in to AWS, so neither can be
dropped quietly later:
  1. the login step turns the masking on;
  2. the role address comes from a *secret*, never from a variable or a literal.
They also run on made-up workflows that break each rule, to show the check does refuse.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
LOGIN_ACTION = "aws-actions/configure-aws-credentials@"

# The four workflows that log in to AWS today. A fifth must be added to the check by being found
# here, so the count is pinned (see test_the_login_steps_are_all_found).
EXPECTED_LOGIN_STEPS = {
    "backup-watch.yml": 1,
    "promote.yml": 1,
    "release.yml": 1,
    "rollback.yml": 1,
}


def _login_steps(doc: dict[str, Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for job in doc.get("jobs", {}).values():
        for step in job.get("steps", []):
            if str(step.get("uses", "")).startswith(LOGIN_ACTION):
                steps.append(step)
    return steps


def problems(doc: dict[str, Any]) -> list[str]:
    """Everything wrong with how this workflow's login steps treat the account number."""
    found: list[str] = []
    for step in _login_steps(doc):
        inputs = step.get("with", {}) or {}
        if str(inputs.get("mask-aws-account-id", "")).lower() != "true":
            found.append("login step does not set mask-aws-account-id: true")
        role = str(inputs.get("role-to-assume", ""))
        if not re.fullmatch(r"\$\{\{\s*secrets\.[A-Z0-9_]+\s*\}\}", role):
            found.append(f"role-to-assume is not a plain secret reference: {role!r}")
    return found


def _workflows() -> dict[str, dict[str, Any]]:
    return {
        p.name: yaml.safe_load(p.read_text(encoding="utf-8"))
        for p in sorted(WORKFLOW_DIR.glob("*.yml"))
    }


def test_the_login_steps_are_all_found() -> None:
    counts = {
        name: len(_login_steps(doc)) for name, doc in _workflows().items() if _login_steps(doc)
    }
    assert counts == EXPECTED_LOGIN_STEPS


@pytest.mark.parametrize("name", sorted(EXPECTED_LOGIN_STEPS))
def test_every_login_step_hides_the_account_number(name: str) -> None:
    assert problems(_workflows()[name]) == []


def test_no_workflow_reads_a_role_or_account_address_from_a_variable() -> None:
    # A variable is printed in the step header; a role ARN or account id there is the leak.
    pattern = re.compile(r"\bvars\.[A-Z0-9_]*(ROLE|ARN|ACCOUNT)[A-Z0-9_]*", re.IGNORECASE)
    for path in sorted(WORKFLOW_DIR.glob("*.yml")):
        assert not pattern.search(path.read_text(encoding="utf-8")), path.name


def test_no_workflow_contains_a_twelve_digit_number() -> None:
    # Nothing in git may hold the account number (CLAUDE.md rule 5). A pinned commit hash can hold
    # 12 digits in a row by chance, so a run next to a hex letter or digit is not counted.
    for path in sorted(WORKFLOW_DIR.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"(?<![0-9a-fA-F])\d{12}(?![0-9a-fA-F])", text), path.name


# -- the check itself must refuse what it is there to refuse (negative cases) -----------------


def _doc(inputs: dict[str, Any]) -> dict[str, Any]:
    return {"jobs": {"j": {"steps": [{"uses": LOGIN_ACTION + "abc", "with": inputs}]}}}


GOOD = {"role-to-assume": "${{ secrets.AWS_PUBLISH_ROLE_ARN }}", "mask-aws-account-id": True}


def test_the_check_accepts_a_correct_login_step() -> None:
    assert problems(_doc(GOOD)) == []


def test_the_check_refuses_masking_left_off() -> None:
    bad = {k: v for k, v in GOOD.items() if k != "mask-aws-account-id"}
    assert any("mask-aws-account-id" in p for p in problems(_doc(bad)))
    assert any(
        "mask-aws-account-id" in p for p in problems(_doc({**GOOD, "mask-aws-account-id": False}))
    )


def test_the_check_refuses_a_role_from_a_variable_or_a_literal() -> None:
    from_var = {**GOOD, "role-to-assume": "${{ vars.AWS_PUBLISH_ROLE_ARN }}"}
    assert any("role-to-assume" in p for p in problems(_doc(from_var)))
    literal = {**GOOD, "role-to-assume": "arn:aws:iam::000000000000:role/x"}
    assert any("role-to-assume" in p for p in problems(_doc(literal)))
