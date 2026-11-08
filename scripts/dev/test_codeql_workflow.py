"""The CodeQL workflow must stay small, pinned and honest (ADR-0080, KAV-110).

  * it runs on every pull request, on main after each merge, once a week and by hand
  * it reads the code and writes only to the repository's Security tab
  * it scans the Python and the workflow files themselves, and builds nothing
  * `init` and `analyze` come from the same release (test_repo_hygiene.py already checks that
    every action in every workflow is pinned to a full commit SHA)
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "codeql.yml"


def _codeql() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _triggers() -> dict:
    return _codeql()[True]  # PyYAML reads the key `on` as the boolean True


def _job() -> dict:
    return _codeql()["jobs"]["analyze"]


def _steps(action: str) -> list[dict]:
    """The job's steps that use one action, whatever release it is pinned to."""
    return [s for s in _job()["steps"] if s.get("uses", "").startswith(action + "@")]


def test_codeql_runs_on_every_pull_request_after_each_merge_and_by_hand() -> None:
    triggers = _triggers()
    assert set(triggers) == {"pull_request", "push", "schedule", "workflow_dispatch"}
    # No filter on pull requests: one with no result could never be made a required check.
    assert not triggers["pull_request"]
    assert triggers["push"] == {"branches": ["main"]}


def test_codeql_also_runs_once_a_week() -> None:
    crons = [entry["cron"] for entry in _triggers()["schedule"]]
    assert len(crons) == 1
    _, _, day_of_month, month, day_of_week = crons[0].split()
    assert (day_of_month, month) == ("*", "*"), "more than once a week"
    assert day_of_week.isdigit(), "not a single weekday"


def test_codeql_can_only_read_the_code_and_write_the_security_tab() -> None:
    assert _codeql()["permissions"] == {}, "the token must start with nothing"
    assert _job()["permissions"] == {"contents": "read", "security-events": "write"}


def test_codeql_scans_python_and_the_workflows_and_builds_nothing() -> None:
    include = _job()["strategy"]["matrix"]["include"]
    assert {row["language"] for row in include} == {"python", "actions"}
    assert {row["build-mode"] for row in include} == {"none"}
    (init,) = _steps("github/codeql-action/init")
    assert init["with"]["languages"] == "${{ matrix.language }}"
    assert init["with"]["build-mode"] == "${{ matrix.build-mode }}"


def test_one_language_failing_does_not_hide_the_other() -> None:
    assert _job()["strategy"]["fail-fast"] is False


def test_codeql_init_and_analyze_come_from_the_same_release() -> None:
    (init,) = _steps("github/codeql-action/init")
    (analyze,) = _steps("github/codeql-action/analyze")
    assert init["uses"].split("@")[1] == analyze["uses"].split("@")[1]


def test_codeql_files_each_language_under_its_own_category() -> None:
    # Two uploads under one category and the second replaces the first.
    (analyze,) = _steps("github/codeql-action/analyze")
    assert analyze["with"]["category"] == "/language:${{ matrix.language }}"


def test_the_codeql_checkout_keeps_no_credentials() -> None:
    (checkout,) = _steps("actions/checkout")
    assert checkout["with"]["persist-credentials"] is False


def test_codeql_has_a_time_limit() -> None:
    assert _job()["timeout-minutes"] <= 30
