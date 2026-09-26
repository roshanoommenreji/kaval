"""Inserts one row per table through the full spine against a real Postgres and reads
it back through the ORM relationships. Skipped automatically if no database is
reachable (e.g. `docker-compose up -d postgres` hasn't been run) rather than failing
the suite — `make dev` brings up the database `make test` needs.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from kaval_shared.db import get_engine
from kaval_shared.models import (
    Action,
    Base,
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
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session


@pytest.fixture
def session() -> Iterator[Session]:
    """One test = one outer transaction, rolled back at teardown, never committed.

    Deliberately does NOT call `Base.metadata.create_all`/`drop_all` — a table set
    owned by Alembic migrations must not also be dropped and recreated by a test
    fixture, which drops the schema out from under any migration running against
    the same database. Instead the session is bound to a connection whose outer
    transaction is rolled back at the end; `join_transaction_mode="create_savepoint"`
    makes the test's own `session.commit()` calls create/release a SAVEPOINT rather
    than the real transaction, so nothing persists either way. (Found by running the
    migration's downgrade test against the same database this fixture had just
    dropped the schema of — a real collision, not a hypothetical one.)
    """
    try:
        engine = get_engine()
        with engine.connect() as probe:
            probe.execute(text("SELECT 1"))
    except (OperationalError, KeyError) as exc:
        pytest.skip(f"no reachable Postgres for the roundtrip test: {exc}")

    connection = engine.connect()
    Base.metadata.create_all(connection)  # no-op if Alembic already created these
    connection.commit()  # closes the autobegun transaction create_all opened
    transaction = connection.begin()
    try:
        with Session(bind=connection, join_transaction_mode="create_savepoint") as db:
            yield db
    finally:
        transaction.rollback()
        connection.close()


def test_one_incident_flows_through_every_table(session: Session) -> None:
    now = datetime.now(UTC)

    signal = Signal(
        source="k8s_event",
        kind="pod_crash_loop",
        target="staging/gateway-7f9c",
        value={"reason": "OOMKilled", "restart_count": 4},
        observed_at=now,
    )
    session.add(signal)
    session.flush()

    incident = Incident(
        fingerprint="pod_crash_loop:gateway",
        severity=Severity.high,
        opened_at=now,
        signals=[signal],
    )
    session.add(incident)
    session.flush()

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
    session.add(proposal)
    session.flush()

    action = Action(
        proposal_id=proposal.id,
        type="restart_pod",
        target="staging/gateway-7f9c",
        params={},
        reversible=True,
        blast_radius=BlastRadius.pod,
        policy_class=PolicyClass.auto,
    )
    session.add(action)
    session.flush()

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
    session.add_all([decision, execution])
    session.flush()

    outcome = Outcome(
        incident_id=incident.id,
        resolved=True,
        mttr_sec=42,
        regression=False,
        measured_at=now,
    )
    session.add(outcome)
    session.commit()

    reloaded = session.get(Incident, incident.id)
    assert reloaded is not None
    assert [s.id for s in reloaded.signals] == [signal.id]

    reloaded_action = reloaded.proposals[0].actions[0]
    assert reloaded_action.decision is not None
    assert reloaded_action.execution is not None
    assert reloaded_action.decision.verdict == Verdict.auto_approved
    assert reloaded_action.execution.status == ExecutionStatus.success
    assert reloaded.outcomes[0].resolved is True
