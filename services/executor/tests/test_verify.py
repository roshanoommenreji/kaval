"""`kaval_executor.verify` and `kaval_executor.mttr`: did the fix hold, and how long did it take
(KAV-77, ADR-0041). Real database, no cluster: the executor's own work is not under test here,
only what is written about it afterwards.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from kaval_executor import mttr, verify
from kaval_shared.models import (
    Action,
    BlastRadius,
    Decision,
    Execution,
    ExecutionStatus,
    Incident,
    IncidentSignal,
    Outcome,
    PolicyClass,
    Proposal,
    RiskLevel,
    Severity,
    Signal,
    Verdict,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

T0 = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)  # the first signal
OPENED = T0 + timedelta(seconds=62)
PROPOSED = T0 + timedelta(seconds=90)
DECIDED = T0 + timedelta(seconds=390)  # a person took five minutes
STARTED = T0 + timedelta(seconds=395)
FINISHED = T0 + timedelta(seconds=397)
JUDGED = FINISHED + verify.VERIFY_AFTER


def _signal(db: Session, workload: str, at: datetime, namespace: str = "kaval-demo",
            kind: str = "pod_oom_killed") -> Signal:
    row = Signal(source="kubernetes", kind=kind, target=f"{namespace}/{workload}-7f9c4d8b6-x2klp",
                 value={"reason": "OOMKilled"}, observed_at=at)
    db.add(row)
    db.flush()
    return row


def _fixed_incident(
    db: Session, *, fingerprint: str | None = None,
    execution: ExecutionStatus = ExecutionStatus.success, verdict: Verdict = Verdict.approved,
) -> tuple[Incident, str]:
    """An incident with one approved, executed fix. Returns it and the workload's unique name."""
    workload = f"checkout-{uuid.uuid4().hex[:6]}"
    incident = Incident(fingerprint=fingerprint or f"oom_killed:k8s:kaval-demo/{workload}",
                        severity=Severity.high, opened_at=OPENED)
    proposal = Proposal(incident=incident, summary="restart", root_cause="oom", confidence=0.9,
                        risk=RiskLevel.low, model="gemma3:1b-it-qat", tokens_in=1, tokens_out=1,
                        cost_usd=Decimal("0"), created_at=PROPOSED)
    action = Action(proposal=proposal, type="restart_pod", target=f"kaval-demo/{workload}",
                    params={}, reversible=True, blast_radius=BlastRadius.pod,
                    policy_class=PolicyClass.ask)
    db.add_all([incident, proposal, action])
    db.flush()
    db.add(Decision(action_id=action.id, verdict=verdict, actor="test", decided_at=DECIDED))
    db.add(Execution(action_id=action.id, status=execution, started_at=STARTED,
                     finished_at=FINISHED))
    first = _signal(db, workload, T0)
    db.add(IncidentSignal(incident_id=incident.id, signal_id=first.id))
    db.flush()
    return incident, workload


def _outcomes(db: Session, incident: Incident) -> list[Outcome]:
    return list(db.scalars(select(Outcome).where(Outcome.incident_id == incident.id)
                           .order_by(Outcome.measured_at)))


def test_a_fix_that_held_is_resolved_with_wall_clock_time_to_restore(db_session: Session) -> None:
    incident, _ = _fixed_incident(db_session)
    done = verify.run_once(db_session, JUDGED)
    assert [(d.resolved, d.mttr_sec) for d in done if d.incident_id == incident.id] == [
        (True, 397)]
    (row,) = _outcomes(db_session, incident)
    assert (row.resolved, row.regression, row.mttr_sec, row.measured_at) == (
        True, False, 397, JUDGED)


def test_a_fix_that_did_not_hold_is_recorded_as_not_resolved(db_session: Session) -> None:
    incident, workload = _fixed_incident(db_session)
    _signal(db_session, workload, FINISHED + timedelta(seconds=120))  # still being killed
    verify.run_once(db_session, JUDGED)
    (row,) = _outcomes(db_session, incident)
    assert (row.resolved, row.mttr_sec) == (False, None)


def test_nothing_is_judged_before_five_minutes_have_passed(db_session: Session) -> None:
    incident, _ = _fixed_incident(db_session)
    verify.run_once(db_session, JUDGED - timedelta(seconds=1))
    assert _outcomes(db_session, incident) == []


def test_a_fix_is_judged_once_not_every_pass(db_session: Session) -> None:
    incident, _ = _fixed_incident(db_session)
    verify.run_once(db_session, JUDGED)
    again = verify.run_once(db_session, JUDGED + timedelta(minutes=1))
    assert [d for d in again if d.incident_id == incident.id] == []
    assert len(_outcomes(db_session, incident)) == 1


def test_a_neighbours_trouble_and_late_noise_do_not_count_against_the_fix(
    db_session: Session,
) -> None:
    incident, workload = _fixed_incident(db_session)
    _signal(db_session, f"other-{uuid.uuid4().hex[:6]}", FINISHED + timedelta(seconds=60))
    _signal(db_session, workload, FINISHED + timedelta(seconds=60), namespace="kaval-other")
    _signal(db_session, workload, FINISHED + verify.VERIFY_AFTER + timedelta(seconds=30))
    verify.run_once(db_session, JUDGED + timedelta(minutes=1))
    (row,) = _outcomes(db_session, incident)
    assert row.resolved is True


def test_a_failed_execution_is_not_judged_at_all(db_session: Session) -> None:
    incident, _ = _fixed_incident(db_session, execution=ExecutionStatus.failed)
    verify.run_once(db_session, JUDGED)
    assert _outcomes(db_session, incident) == []


def test_an_incident_that_is_not_kubernetes_is_left_alone(db_session: Session) -> None:
    incident, _ = _fixed_incident(db_session, fingerprint=f"cost_spike:aws:{uuid.uuid4().hex}")
    verify.run_once(db_session, JUDGED)
    assert _outcomes(db_session, incident) == []


def test_a_fix_that_held_then_came_back_is_flagged_once_as_a_regression(
    db_session: Session,
) -> None:
    incident, _ = _fixed_incident(db_session)
    verify.run_once(db_session, JUDGED)
    db_session.add(Incident(fingerprint=incident.fingerprint, severity=Severity.high,
                            opened_at=JUDGED + timedelta(minutes=40)))
    db_session.flush()
    later = JUDGED + timedelta(minutes=45)
    first = verify.flag_regressions(db_session, later)
    second = verify.flag_regressions(db_session, later + timedelta(minutes=1))
    assert [(d.regression, d.resolved) for d in first
            if d.incident_id == incident.id] == [(True, False)]
    assert [d for d in second if d.incident_id == incident.id] == []
    assert [o.regression for o in _outcomes(db_session, incident)] == [False, True]


def test_a_quiet_fix_is_not_a_regression(db_session: Session) -> None:
    incident, _ = _fixed_incident(db_session)
    verify.run_once(db_session, JUDGED)
    verify.flag_regressions(db_session, JUDGED + timedelta(hours=1))
    assert len(_outcomes(db_session, incident)) == 1


# ── mttr ────────────────────────────────────────────────────────────────────────────────

def test_stages_add_up_to_the_stored_wall_clock_and_the_machine_share_excludes_the_human(
    db_session: Session,
) -> None:
    incident, _ = _fixed_incident(db_session)
    verify.run_once(db_session, JUDGED)
    (row,) = [t for t in mttr.timings(db_session) if t.fingerprint == incident.fingerprint]
    assert (row.detect, row.diagnose, row.human, row.queue, row.execute) == (62, 28, 300, 5, 2)
    assert row.wall == 397 == _outcomes(db_session, incident)[0].mttr_sec
    assert row.machine == 97


def test_an_auto_approved_fix_has_no_human_wait(db_session: Session) -> None:
    incident, _ = _fixed_incident(db_session, verdict=Verdict.auto_approved)
    verify.run_once(db_session, JUDGED)
    (row,) = [t for t in mttr.timings(db_session) if t.fingerprint == incident.fingerprint]
    assert row.human == 0 and row.machine == row.wall


def test_a_fix_that_did_not_hold_is_not_in_the_time_to_restore(db_session: Session) -> None:
    incident, workload = _fixed_incident(db_session)
    _signal(db_session, workload, FINISHED + timedelta(seconds=30))
    verify.run_once(db_session, JUDGED)
    assert incident.fingerprint not in [t.fingerprint for t in mttr.timings(db_session)]


def test_percentile_is_nearest_rank_and_reports_the_worst_case() -> None:
    values = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 1000.0]
    assert mttr.percentile(values, 0.9) == 90.0
    assert mttr.percentile(values, 1.0) == 1000.0
    assert mttr.percentile([7.0], 0.9) == 7.0


def test_the_report_says_so_when_there_is_nothing_to_report() -> None:
    assert mttr.render([]) == ["no resolved incidents yet"]


def test_the_report_gives_machine_and_wall_clock_separately() -> None:
    rows = [mttr.Timing("oom_killed:k8s:kaval-demo/a", 60, 30, 300, 5, 2)]
    text = "\n".join(mttr.render(rows))
    assert "machine" in text and "wall-clock" in text and "397s" in text and "97s" in text


@pytest.mark.parametrize("fingerprint, expected", [
    ("oom_killed:k8s:kaval-demo/checkout", "kaval-demo/checkout"),
    ("cost_spike:aws:ec2", None),
    ("not-a-fingerprint", None),
])
def test_only_kubernetes_fingerprints_name_a_workload(
    fingerprint: str, expected: str | None,
) -> None:
    assert verify._k8s_subject(fingerprint) == expected
