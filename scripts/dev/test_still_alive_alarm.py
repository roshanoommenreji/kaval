"""The still-alive alarm's rule is the one the ADR and the runbook describe (KAV-83, ADR-0053).

CloudWatch is not run here (the live proof is Lab 50 and a read-only test of the expression against
real empty data was made before it was written); these check that the settings that carry the
rule cannot be changed by accident: a parked server stays silent, a missing reading leans toward
ringing, and the quiet time is the documented 25 minutes."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAIN = (ROOT / "infra" / "modules" / "still-alive" / "main.tf").read_text(encoding="utf-8")
VARS = (ROOT / "infra" / "modules" / "still-alive" / "variables.tf").read_text(encoding="utf-8")


def test_the_rule_is_wants_a_server_and_silent() -> None:
    assert 'expression  = "IF(FILL(wanted, 1) > 0 AND FILL(heartbeat, 0) < 1, 1, 0)"' in MAIN


def test_a_missing_reading_leans_toward_ringing() -> None:
    # missing group size -> "wants a server" (1); missing heartbeat -> "no beats" (0); missing
    # everything -> ring. Flipping either fill would make a lost reading look like health.
    assert "FILL(wanted, 1)" in MAIN and "FILL(heartbeat, 0)" in MAIN
    assert 'treat_missing_data = "breaching"' in MAIN


def test_it_needs_every_period_to_be_silent_not_just_one() -> None:
    assert "evaluation_periods  = var.silent_periods" in MAIN
    assert "datapoints_to_alarm = var.silent_periods" in MAIN


def test_the_default_quiet_time_is_the_documented_25_minutes() -> None:
    period = int(re.search(r'variable "period_seconds".*?default\s*=\s*(\d+)', VARS, re.S)[1])
    silent = int(re.search(r'variable "silent_periods".*?default\s*=\s*(\d+)', VARS, re.S)[1])
    assert period * silent == 25 * 60


def test_it_tells_a_person_when_it_rings_and_when_it_clears() -> None:
    assert "alarm_actions = [aws_sns_topic.still_alive.arn]" in MAIN
    assert "ok_actions    = [aws_sns_topic.still_alive.arn]" in MAIN


def test_the_node_may_only_put_the_one_metric_namespace() -> None:
    iam = (ROOT / "infra" / "modules" / "iam" / "main.tf").read_text(encoding="utf-8")
    assert '"cloudwatch:PutMetricData"' in iam
    assert '"cloudwatch:namespace" = "Kaval/Heartbeat"' in iam
    # it must not be able to touch alarms or read metrics
    assert "cloudwatch:SetAlarmState" not in iam and "cloudwatch:DeleteAlarms" not in iam
