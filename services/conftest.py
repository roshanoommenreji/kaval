"""Fixtures shared by every service's tests.

`db_session` is the one way a test touches Postgres. Tests that use it are skipped, not
failed, when no database is reachable, so `make test` stays green on a laptop with the
dev server stopped; `make dev` + `make dev-tunnel` bring the database up.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from kaval_shared.db import get_engine
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session


@pytest.fixture(scope="session")
def migrated_engine() -> Engine:
    """Probe the database once per run, not once per test.

    With nothing listening, a connect to `localhost` on Windows takes ~10 s to fail (SYN
    retries, over IPv6 and then IPv4), which made 12 skipping tests cost two minutes. A
    session-scoped fixture caches its skip, so every later test skips at once.
    """
    try:
        engine = get_engine()
        with engine.connect() as probe:
            probe.execute(text("SELECT version_num FROM alembic_version"))
    except (OperationalError, KeyError) as exc:
        reason = f"no reachable, migrated Postgres: {type(exc).__name__}"
        # CI sets KAVAL_REQUIRE_DB=1: there, a missing database is a broken pipeline, and a
        # skip would let 12 tests vanish from a green run without anyone noticing (KAV-24).
        if os.environ.get("KAVAL_REQUIRE_DB") == "1":
            pytest.fail(reason, pytrace=False)
        pytest.skip(reason)
    return engine


@pytest.fixture
def db_session(migrated_engine: Engine) -> Iterator[Session]:
    """One test = one outer transaction, rolled back at teardown, never committed.

    Deliberately does NOT call `Base.metadata.create_all`/`drop_all`: the table set is
    owned by Alembic migrations, and a fixture that drops and recreates it pulls the
    schema out from under any migration running against the same database. Instead the
    session is bound to a connection whose outer transaction is rolled back at the end;
    `join_transaction_mode="create_savepoint"` turns the code under test's own
    `session.commit()` calls into SAVEPOINTs, so nothing persists either way. (Found by
    running the migration's downgrade test against a database an earlier version of this
    fixture had just dropped the schema of: a real collision, not a hypothetical one.)
    """
    connection = migrated_engine.connect()
    transaction = connection.begin()
    try:
        with Session(bind=connection, join_transaction_mode="create_savepoint") as db:
            yield db
    finally:
        transaction.rollback()
        connection.close()
