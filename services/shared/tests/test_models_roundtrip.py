"""Inserts one row per table through the full spine against a real Postgres and reads
it back through the ORM relationships. Skipped automatically if no database is
reachable rather than failing the suite: the `db_session` fixture in services/conftest.py
handles that, and `make dev` + `make dev-tunnel` bring up the database it needs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from kaval_shared.models import (
    Action,
    BlastRadius,
    Decision,
    Execution,
    ExecutionStatus,
    Incident,
    Outcome,
    PolicyClass,
    Proposal,
    RiskLevel,
    Severity,
    Signal,
    Verdict,
)
from sqlalchemy.orm import Session


def test_one_incident_flows_through_every_table(db_session: Session) -> None:
    now = datetime.now(UTC)

    signal = Signal(
        source="k8s_event",
        kind="pod_crash_loop",
        target="staging/gateway-7f9c",
        value={"reason": "OOMKilled", "restart_count": 4},
        observed_at=now,
    )
    db_session.add(signal)
    db_session.flush()

    incident = Incident(
        fingerprint="pod_crash_loop:gateway",
        severity=Severity.high,
        opened_at=now,
        signals=[signal],
    )
    db_session.add(incident)
    db_session.flush()

    proposal = Proposal(
        incident_id=incident.id,
        summary="Restart the crash-looping gateway pod",
        root_cause="OOMKilled: memory limit too low for the current load",
        confidence=0.93,
        risk=RiskLevel.low,
        model="gemma3:1b",
        tokens_in=512,
        tokens_out=128,
        cost_usd=Decimal("0.000000"),
    )
    db_session.add(proposal)
    db_session.flush()

    action = Action(
        proposal_id=proposal.id,
        type="restart_pod",
        target="staging/gateway-7f9c",
        params={},
        reversible=True,
        blast_radius=BlastRadius.pod,
        policy_class=PolicyClass.auto,
    )
    db_session.add(action)
    db_session.flush()

    decision = Decision(
        action_id=action.id,
        verdict=Verdict.auto_approved,
        actor="system",
        decided_at=now,
    )
    execution = Execution(
        action_id=action.id,
        status=ExecutionStatus.success,
        stdout="pod/gateway-7f9c deleted",
        started_at=now,
        finished_at=now,
    )
    db_session.add_all([decision, execution])
    db_session.flush()

    outcome = Outcome(
        incident_id=incident.id,
        resolved=True,
        mttr_sec=42,
        regression=False,
        measured_at=now,
    )
    db_session.add(outcome)
    db_session.commit()

    reloaded = db_session.get(Incident, incident.id)
    assert reloaded is not None
    assert [s.id for s in reloaded.signals] == [signal.id]

    reloaded_action = reloaded.proposals[0].actions[0]
    assert reloaded_action.decision is not None
    assert reloaded_action.execution is not None
    assert reloaded_action.decision.verdict == Verdict.auto_approved
    assert reloaded_action.execution.status == ExecutionStatus.success
    assert reloaded.outcomes[0].resolved is True
