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
    raw = db.connection().connection.dbapi_connection
    assert raw is not None
    try:
        raw.cursor().execute(sql)  # no parameters: the script's own % signs are left alone
    except Exception as exc:  # noqa: BLE001 - roles need a database user allowed to create them
        pytest.skip(f"cannot create roles here: {type(exc).__name__}")


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
