"""The monthly check-in workflow must stay small, pinned and powerless (ADR-0106, KAV-168).

  * it runs on the 1st of the month and by hand, and nothing else
  * the token starts with nothing; its one job can only write issues
  * it has no AWS login, no secret, and does not check out the code
  * it never pastes an expression into a shell command
  * it opens at most one issue per month, and says so when older ones are still open
  * the runbook it points at exists and holds the dated reminders
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "monthly-check-in.yml"
RUNBOOK = ROOT / "docs" / "runbooks" / "monthly-check-in.md"


def _wf() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _run() -> str:
    steps = _wf()["jobs"]["open"]["steps"]
    return next(s for s in steps if "run" in s)["run"]


def test_it_runs_on_the_first_of_the_month_and_by_hand_only() -> None:
    triggers = _wf()[True]  # PyYAML reads the key `on` as the boolean True
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    crons = [entry["cron"] for entry in triggers["schedule"]]
    assert len(crons) == 1
    _, _, day_of_month, month, day_of_week = crons[0].split()
    assert (day_of_month, month, day_of_week) == ("1", "*", "*"), "not the 1st of every month"
    assert triggers["workflow_dispatch"] in (None, {}), "a hand run takes no input"


def test_the_token_starts_with_nothing_and_the_job_can_only_write_issues() -> None:
    wf = _wf()
    assert wf["permissions"] == {}
    assert wf["jobs"]["open"]["permissions"] == {"issues": "write"}


def test_no_aws_login_no_secret_and_no_checkout() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "configure-aws-credentials" not in code
    assert "secrets." not in code
    assert "id-token" not in code
    assert "actions/checkout" not in code, "nothing from the repository is read or run"


def test_no_expression_is_pasted_into_the_script() -> None:
    assert "${{" not in _run(), "an expression pasted into the script is an injection hole"


def test_one_issue_a_month_and_older_open_ones_are_called_out() -> None:
    run = _run()
    assert "already exists" in run and "exit 0" in run, "a re-run must not open a second issue"
    assert "still open" in run
    assert "gh issue create" in run


def test_the_runbook_exists_and_carries_the_dated_reminders() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "2027-02-14" in text, "the laptop key's next change"
    assert "November 2027" in text, "the first real secret rotation"
    assert "gh workflow list" in text, "the 60-day switch-off check"
    assert "docs/runbooks/monthly-check-in.md" in _run(), "the issue points at the runbook"
