"""Connection setup, built from the POSTGRES_* variables every service already reads.

Sync SQLAlchemy for now — nothing in Phase 1 needs an async driver, and the models work
unchanged under either. Revisit if the gateway's request volume ever makes it matter.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def database_url() -> str:
    user = os.environ["POSTGRES_USER"]
    password = os.environ["POSTGRES_PASSWORD"]
    host = os.environ["POSTGRES_HOST"]
    port = os.environ["POSTGRES_PORT"]
    db = os.environ["POSTGRES_DB"]
    # Unset locally/in dev (no TLS there). The standalone database server (ADR-0008, KAV-32)
    # sets this to "require" from its own out-of-band Secret.
    sslmode = os.environ.get("POSTGRES_SSLMODE", "prefer")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db}?sslmode={sslmode}"


@lru_cache
def get_engine() -> Engine:
    # A bounded connect_timeout matters here: without one, a Postgres that's down or
    # unreachable (wrong host, docker-compose not started) hangs for minutes before
    # failing instead of erroring immediately — found by the test suite doing exactly
    # that against a closed port.
    return create_engine(
        database_url(), pool_pre_ping=True, connect_args={"connect_timeout": 5}
    )


@lru_cache
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI-dependency-shaped: `Depends(get_session)` yields one session per request."""
    session = _session_factory()()
    try:
        yield session
    finally:
        session.close()
