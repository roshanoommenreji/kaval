"""The verdict of `chaos_check.py` (KAV-81): a pass needs an incident, a message to a person, no
automatic proposal, every hand-filed action refused as `never`, and nothing executed."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import chaos_check  # noqa: E402
from chaos_check import Report  # noqa: E402


def _good() -> Report:
    return Report(incidents=["disk_pressure:k8s:kaval-staging/node-disk"], messages_prepared=1,
                  by_hand_proposals=1)


def test_told_a_person_and_nothing_proposed_passes() -> None:
    assert chaos_check.judge(_good()) == []


def test_a_hand_filed_never_action_that_was_refused_still_passes() -> None:
    report = _good()
    report.by_hand_proposals = 2
    assert chaos_check.judge(report) == []


def test_silence_fails() -> None:
    report = _good()
    report.messages_sent = report.messages_prepared = 0
    assert any("no message" in p for p in chaos_check.judge(report))


def test_no_incident_fails() -> None:
    assert any("no disk or slowness incident" in p for p in chaos_check.judge(Report()))


def test_an_automatic_proposal_fails() -> None:
    report = _good()
    report.automated_proposals = 1
    assert any("automatic path" in p for p in chaos_check.judge(report))


def test_a_hand_filed_action_that_was_not_never_fails() -> None:
    report = _good()
    report.by_hand_not_never = 1
    assert any("not classed never" in p for p in chaos_check.judge(report))


def test_any_execution_fails() -> None:
    report = _good()
    report.executions = 1
    assert any("executed" in p for p in chaos_check.judge(report))
