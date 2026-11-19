"""The weekly image watch workflow must stay small, pinned and powerless (ADR-0106, KAV-167).

  * it runs once a week and by hand, and nothing else
  * the token starts with nothing; `scan` can only read the code, `tell` can only write issues
  * it has no AWS login and no secret of any kind: it reads git and public registries
  * a failed vulnerability-list download is retried, then fails the run
  * the report (CVE ids and package names) is read from an environment variable, never pasted
    into a shell command
  * test_repo_hygiene.py already checks that every action is pinned to a full commit SHA
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "image-watch.yml"


def _wf() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_it_runs_once_a_week_and_by_hand_only() -> None:
    triggers = _wf()[True]  # PyYAML reads the key `on` as the boolean True
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    crons = [entry["cron"] for entry in triggers["schedule"]]
    assert len(crons) == 1
    _, _, day_of_month, month, day_of_week = crons[0].split()
    assert (day_of_month, month) == ("*", "*") and day_of_week.isdigit(), "not a single weekday"


def test_the_only_hand_input_is_the_plant_switch_and_it_is_a_boolean() -> None:
    inputs = _wf()[True]["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"plant"} and inputs["plant"]["type"] == "boolean"


def test_each_job_has_exactly_the_power_it_needs() -> None:
    wf = _wf()
    assert wf["permissions"] == {}, "the token must start with nothing"
    assert wf["jobs"]["scan"]["permissions"] == {"contents": "read"}
    assert wf["jobs"]["tell"]["permissions"] == {"issues": "write"}


def test_no_aws_login_and_no_secret_anywhere() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "configure-aws-credentials" not in code
    assert "secrets." not in code
    assert "id-token" not in code


def test_the_vulnerability_list_download_is_retried_then_fails_the_run() -> None:
    steps = _wf()["jobs"]["scan"]["steps"]
    fetch = next(s for s in steps if "download-db-only" in s.get("run", ""))
    assert "for attempt in" in fetch["run"] and "exit 1" in fetch["run"]


def test_trivy_is_downloaded_and_checked_against_the_pinned_checksum() -> None:
    steps = _wf()["jobs"]["scan"]["steps"]
    install = next(s for s in steps if "trivy.tgz" in s.get("run", ""))
    assert "sha256sum --check --strict" in install["run"]
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    env = _wf()["env"]
    assert f'TRIVY_VERSION: "{env["TRIVY_VERSION"]}"' in ci, "a different Trivy than CI"
    assert f'TRIVY_SHA256: "{env["TRIVY_SHA256"]}"' in ci, "a different checksum than CI"


def test_the_report_reaches_the_shell_only_through_an_environment_variable() -> None:
    step = next(s for s in _wf()["jobs"]["tell"]["steps"] if "run" in s)
    assert "needs.scan.outputs.report" in step["env"]["REPORT"]
    assert "${{" not in step["run"], "an expression pasted into the script is an injection hole"


def test_a_clean_run_closes_the_issue_and_a_finding_turns_the_run_red() -> None:
    run = next(s for s in _wf()["jobs"]["tell"]["steps"] if "run" in s)["run"]
    assert "gh issue close" in run
    assert run.rstrip().endswith("exit 1")
