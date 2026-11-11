"""What each service's database role may and may not do (ADR-0008, KAV-82), tested by running the
real `scripts/ops/db-roles.sql` against the test database inside the test's transaction (roles
are rolled back with everything else).

Why this exists: nothing ran that file in a test, and the gateway's Slack notifier was found to
update a column its role had no right to write. The post succeeded and the bookkeeping update was
refused, which in production would have re-posted the same approval every 30 seconds.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "ops" / "db-roles.sql"


def _apply_roles(db: Session) -> None:
    sql = SCRIPT.read_text(encoding="utf-8")
    sql = sql.replace(chr(92) + "set ON_ERROR_STOP on", "")  # a psql command, not SQL
    sql = re.sub(r":'(\w+_password)'", "'test-password'", sql)
    # psql's "keep the result" command; read as plain SQL it is just the end of the statement.
    sql = sql.replace(" " + chr(92) + "gset", ";")
    raw = db.connection().connection.dbapi_connection
    assert raw is not None
    try:
        raw.cursor().execute(sql)  # no parameters: the script's own % signs are left alone
    except Exception as exc:  # noqa: BLE001 - see below: only one reason may skip
        # Only "this database user may not create roles" (SQLSTATE 42501) is a reason to skip.
        # Anything else, a syntax error after an edit to the script for one, must fail loudly:
        # KAV-125 added a psql-only line and all five tests below skipped without anyone noticing.
        if getattr(exc, "sqlstate", None) == "42501":
            pytest.skip(f"cannot create roles here: {type(exc).__name__}")
        raise


def _can(db: Session, role: str, statement: str) -> bool:
    savepoint = db.begin_nested()
    db.execute(text(f"SET LOCAL ROLE {role}"))
    try:
        db.execute(text(statement))
        return True
    except ProgrammingError:
        return False
    finally:
        savepoint.rollback()


def test_the_gateway_may_mark_a_slack_message_on_an_action_and_nothing_else(
    db_session: Session,
) -> None:
    _apply_roles(db_session)

    assert _can(db_session, "kaval_gateway",
                "UPDATE action SET slack_notified_at = now(), slack_ts = '1.2' WHERE false")
    # Least privilege: it still cannot change what an action is or how it is classed.
    assert not _can(db_session, "kaval_gateway",
                    "UPDATE action SET policy_class = 'auto' WHERE false")
    assert not _can(db_session, "kaval_gateway", "UPDATE action SET target = 'x' WHERE false")
    assert not _can(db_session, "kaval_gateway", "DELETE FROM action WHERE false")


def test_the_gateway_can_record_what_it_told_people_but_not_rewrite_it(
    db_session: Session,
) -> None:
    _apply_roles(db_session)

    assert _can(db_session, "kaval_gateway",
                "INSERT INTO notification (id, key, message) VALUES (gen_random_uuid(), 'k', 'm')")
    assert not _can(db_session, "kaval_gateway", "UPDATE notification SET message = 'x'")
    assert not _can(db_session, "kaval_gateway", "DELETE FROM notification")


def test_the_gateway_records_and_reads_decisions_but_can_never_change_one(
    db_session: Session,
) -> None:
    """KAV-116 (threat model T-APPROVE-4): a verdict is written once. The gateway's login could
    also UPDATE the table, though no code does, so a confused or hijacked gateway could have turned
    a "denied" into an "approved" after the fact. Recording and reading is all it gets."""
    _apply_roles(db_session)

    # `WHERE false` / `SELECT ... WHERE false`: the right is checked before any row is touched, so
    # no action row is needed for this to tell "refused" from "allowed".
    assert _can(db_session, "kaval_gateway",
                "INSERT INTO decision (id, action_id, verdict, actor, decided_at) "
                "SELECT gen_random_uuid(), gen_random_uuid(), 'approved', 'x', now() WHERE false")
    assert _can(db_session, "kaval_gateway", "SELECT id, verdict, actor FROM decision WHERE false")
    assert not _can(db_session, "kaval_gateway", "UPDATE decision SET verdict = 'approved'")
    assert not _can(db_session, "kaval_gateway", "UPDATE decision SET actor = 'x' WHERE false")
    assert not _can(db_session, "kaval_gateway", "DELETE FROM decision")
    assert not _can(db_session, "kaval_gateway", "TRUNCATE decision")


def test_the_real_approval_code_still_works_as_the_gateways_own_login(db_session: Session) -> None:
    """The point of the change is to take UPDATE away without breaking the one thing the gateway
    does with this table. So the real `record_decision` runs here as `kaval_gateway` itself (not as
    the all-powerful test user, which could not see a missing right, KAV-95): it must record a
    verdict, read it back, and refuse a second one."""
    from datetime import UTC, datetime
    from decimal import Decimal

    from kaval_gateway.decisions import DecisionOutcome, record_decision
    from kaval_shared.models import (
        Action,
        BlastRadius,
        Incident,
        PolicyClass,
        Proposal,
        RiskLevel,
        Severity,
        Verdict,
    )

    _apply_roles(db_session)
    incident = Incident(fingerprint="kav116-role-test", severity=Severity.high,
                        opened_at=datetime.now(UTC), signals=[])
    proposal = Proposal(incident=incident, summary="restart checkout", root_cause="memory",
                        confidence=0.8, risk=RiskLevel.low, model="test", tokens_in=1,
                        tokens_out=1, cost_usd=Decimal("0"))
    action = Action(proposal=proposal, type="rollout_restart", target="ns/deploy/x", params={},
                    reversible=True, blast_radius=BlastRadius.pod, policy_class=PolicyClass.ask)
    db_session.add_all([incident, proposal, action])
    db_session.flush()
    action_id = action.id

    db_session.execute(text("SET LOCAL ROLE kaval_gateway"))
    outcome, decision = record_decision(db_session, action_id, Verdict.denied, actor="roshan")
    assert outcome is DecisionOutcome.recorded and decision is not None
    assert decision.verdict == Verdict.denied
    again, _ = record_decision(db_session, action_id, Verdict.approved, actor="someone-else")
    assert again is DecisionOutcome.already_decided
    stored = db_session.execute(
        text("SELECT verdict FROM decision WHERE action_id = :a"), {"a": action_id}).scalar_one()
    assert stored == "denied"  # and the second, "approved", never overwrote it


def test_a_database_that_already_has_the_old_update_right_loses_it(db_session: Session) -> None:
    """The case a fresh test database hides. Production's database was given UPDATE on `decision`
    by an earlier run of the script, and a GRANT is never undone by deleting its line. So the script
    must say REVOKE out loud. This starts from the old state, runs the script, and checks it is
    gone."""
    _apply_roles(db_session)
    # How the database used to be:
    db_session.execute(text("GRANT UPDATE, DELETE ON decision TO kaval_gateway"))
    assert _can(db_session, "kaval_gateway",
                "UPDATE decision SET verdict = 'approved' WHERE false")

    _apply_roles(db_session)  # the next start-up runs the script again

    assert not _can(db_session, "kaval_gateway", "UPDATE decision SET verdict = 'approved'")
    assert not _can(db_session, "kaval_gateway", "DELETE FROM decision")
    # ...and it did not take away what the gateway does need.
    assert _can(db_session, "kaval_gateway", "SELECT id FROM decision WHERE false")


def test_the_script_never_grants_the_gateway_update_on_decision() -> None:
    """The cheap guard, readable without a database: a line that hands UPDATE on `decision` to the
    gateway fails here even on a laptop where the database tests above are skipped."""
    for raw in SCRIPT.read_text(encoding="utf-8").splitlines():
        line = raw.split("--", 1)[0].strip().upper()
        if line.startswith("GRANT") and " ON DECISION" in line and "KAVAL_GATEWAY" in line:
            words = set(line.split(" ON DECISION", 1)[0].replace(",", " ").split())
            assert not words & {"UPDATE", "DELETE", "TRUNCATE", "ALL"}, raw


# Everything each service's login may do, table by table (ADR-0008). A table or right missing here
# means "none": so a new table, or a right added by a slip, fails this test until someone writes it
# down on purpose. Column-level grants (the gateway's two columns on `action`) are not table rights
# and are checked on their own above.
_S, _I, _U, _D = "SELECT", "INSERT", "UPDATE", "DELETE"
EXPECTED_RIGHTS: dict[str, dict[str, set[str]]] = {
    "kaval_collector": {"signal": {_S, _I}},
    "kaval_agent": {
        "signal": {_S}, "incident": {_S, _I, _U}, "proposal": {_S, _I, _U},
        "action": {_S, _I, _U}, "incident_signal": {_S, _I},
        "runbook_chunk": {_S, _I, _U, _D}, "outcome": {_S},
        "decision": {_S}, "execution": {_S},
    },
    "kaval_executor": {
        "action": {_S}, "decision": {_S}, "execution": {_S, _I, _U}, "outcome": {_S, _I, _U},
        "signal": {_S}, "incident": {_S}, "incident_signal": {_S}, "proposal": {_S},
    },
    "kaval_gateway": {
        "incident": {_S}, "proposal": {_S}, "action": {_S}, "execution": {_S}, "outcome": {_S},
        "decision": {_S, _I}, "signal": {_S}, "incident_signal": {_S}, "alembic_version": {_S},
        "notification": {_S, _I},
    },
}


def test_every_login_has_exactly_the_table_rights_written_down(db_session: Session) -> None:
    _apply_roles(db_session)
    tables = [row[0] for row in db_session.execute(text(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1"))]
    assert tables, "no tables found, so this test would prove nothing"

    actual: dict[str, dict[str, set[str]]] = {}
    for role in EXPECTED_RIGHTS:
        for table in tables:
            for right in (_S, _I, _U, _D):
                allowed = db_session.execute(
                    text("SELECT has_table_privilege(:r, :t, :p)"),
                    {"r": role, "t": f"public.{table}", "p": right}).scalar()
                if allowed:
                    actual.setdefault(role, {}).setdefault(table, set()).add(right)

    assert actual == EXPECTED_RIGHTS


def test_the_executor_cannot_approve_anything(db_session: Session) -> None:
    _apply_roles(db_session)

    assert not _can(db_session, "kaval_executor",
                    "INSERT INTO decision (id, action_id, verdict, actor, decided_at) "
                    "VALUES (gen_random_uuid(), gen_random_uuid(), 'approved', 'x', now())")


def test_the_agent_can_read_everything_the_diagnosis_worker_reads_but_cannot_write_outcomes(
    db_session: Session,
) -> None:
    """KAV-87: on staging the worker was refused 'permission denied for table outcome' as soon as a
    second similar incident existed, because the context builder reads how past fixes turned out.
    Every table the worker reads is listed here, so the next one added cannot be missed quietly."""
    _apply_roles(db_session)

    for table in ("incident", "proposal", "action", "outcome", "signal", "incident_signal",
                  "runbook_chunk"):
        assert _can(db_session, "kaval_agent", f"SELECT 1 FROM {table} WHERE false"), table
    # Least privilege: it reads how fixes turned out, it never records or edits that.
    assert not _can(db_session, "kaval_agent", "INSERT INTO outcome DEFAULT VALUES")
    assert not _can(db_session, "kaval_agent", "UPDATE outcome SET resolved = true")
    assert not _can(db_session, "kaval_agent", "DELETE FROM outcome")


def test_the_removal_planner_can_read_approvals_and_executions_but_never_write_them(
    db_session: Session,
) -> None:
    """KAV-95, found live on staging 2026-10-19: the planner (it runs in the diagnosis worker, so as
    kaval_agent) failed every pass with 'permission denied for table decision'. To never ask twice
    it looks up whether a step was already approved, denied or handed over. The end-to-end test ran
    as an all-powerful user and could not see it. Reading is all it gets."""
    _apply_roles(db_session)

    # The planner's own lookup: an action with its decision and its execution.
    assert _can(db_session, "kaval_agent",
                "SELECT a.id, d.verdict, e.id FROM action a "
                "LEFT JOIN decision d ON d.action_id = a.id "
                "LEFT JOIN execution e ON e.action_id = a.id WHERE false")
    for table in ("decision", "execution"):
        assert not _can(db_session, "kaval_agent", f"INSERT INTO {table} DEFAULT VALUES"), table
        assert not _can(db_session, "kaval_agent", f"UPDATE {table} SET id = id"), table
        assert not _can(db_session, "kaval_agent", f"DELETE FROM {table}"), table
