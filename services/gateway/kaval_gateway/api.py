"""The /v1 REST surface: read-only views of signals and incidents (KAV-23).

Read-only on purpose. The gateway's writes (a human's approve/deny becoming a `decision`
row) arrive with the approval flow; until then nothing here can change the audit trail,
and `read_session` makes the database enforce that, not just the code.

Lists are newest first and paged with an opaque cursor, not `?page=N`. Signals arrive
continuously, so offset paging would skip or repeat rows as new ones land between two
requests; a cursor that says "older than this row" can't.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from collections.abc import Generator
from datetime import datetime
from typing import Annotated, Literal, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query
from kaval_shared.db import get_engine
from kaval_shared.models import Action, Incident, Proposal, Signal
from sqlalchemy import Select, literal, select, text, tuple_
from sqlalchemy.orm import InstrumentedAttribute, Session, selectinload

from kaval_gateway.schemas import (
    IncidentDetail,
    IncidentPage,
    IncidentSummary,
    SignalOut,
    SignalPage,
)

router = APIRouter(prefix="/v1")
Row = TypeVar("Row", Signal, Incident)


def read_session() -> Generator[Session, None, None]:
    """One session per request, inside a READ ONLY transaction.

    Postgres itself refuses any INSERT/UPDATE/DELETE on it, so a bug in a read endpoint
    can't write. Per-service database roles (ADR-0008, Phase 4) make the same guarantee
    at the credential level; this is the layer that exists today.
    """
    with Session(get_engine()) as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        yield session


DB = Annotated[Session, Depends(read_session)]
Limit = Annotated[int, Query(ge=1, le=200, description="Page size")]
Cursor = Annotated[str | None, Query(description="next_cursor from the previous page")]


def _encode_cursor(ts: datetime, row_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{ts.isoformat()}|{row_id}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        ts, row_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        return datetime.fromisoformat(ts), uuid.UUID(row_id)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc


def _page(
    db: Session,
    query: Select[Row],  # SQLAlchemy 2.1 typing: Select[Row], was Select[tuple[Row]]
    ts: InstrumentedAttribute[datetime],
    row_id: InstrumentedAttribute[uuid.UUID],
    limit: int,
    cursor: str | None,
) -> tuple[list[Row], str | None]:
    """Newest first by (timestamp, id): the id breaks ties so no row is ever skipped."""
    if cursor:
        after_ts, after_id = _decode_cursor(cursor)
        query = query.where(tuple_(ts, row_id) < tuple_(literal(after_ts), literal(after_id)))
    rows = list(db.scalars(query.order_by(ts.desc(), row_id.desc()).limit(limit + 1)))
    if len(rows) <= limit:
        return rows, None
    last = rows[limit - 1]
    return rows[:limit], _encode_cursor(getattr(last, ts.key), last.id)


@router.get("/signals", response_model=SignalPage, tags=["signals"])
def list_signals(
    db: DB,
    limit: Limit = 50,
    cursor: Cursor = None,
    source: str | None = None,
    kind: str | None = None,
    target: str | None = None,
    synthetic: Annotated[
        bool | None, Query(description="true: only fakes · false: only real · omit: both")
    ] = None,
    run_id: Annotated[str | None, Query(description="One synthetic run's signals")] = None,
) -> SignalPage:
    """Raw observations, newest first. Nothing here is interpreted yet."""
    query = select(Signal)
    for column, wanted in ((Signal.source, source), (Signal.kind, kind),
                           (Signal.target, target)):
        if wanted is not None:
            query = query.where(column == wanted)
    if synthetic is True:
        query = query.where(Signal.value.contains({"synthetic": True}))
    elif synthetic is False:
        query = query.where(~Signal.value.contains({"synthetic": True}))
    if run_id is not None:
        query = query.where(Signal.value.contains({"run_id": run_id}))
    rows, next_cursor = _page(db, query, Signal.observed_at, Signal.id, limit, cursor)
    return SignalPage(items=[SignalOut.model_validate(r) for r in rows], next_cursor=next_cursor)


@router.get("/signals/{signal_id}", response_model=SignalOut, tags=["signals"],
            responses={404: {"description": "No such signal"}})
def get_signal(signal_id: uuid.UUID, db: DB) -> SignalOut:
    signal = db.get(Signal, signal_id)
    if signal is None:
        raise HTTPException(status_code=404, detail="signal not found")
    return SignalOut.model_validate(signal)


@router.get("/incidents", response_model=IncidentPage, tags=["incidents"])
def list_incidents(
    db: DB,
    limit: Limit = 50,
    cursor: Cursor = None,
    status: Literal["open", "closed"] | None = None,
) -> IncidentPage:
    """Correlated problems, newest first. Empty until correlation lands in Phase 2."""
    query = select(Incident)
    if status == "open":
        query = query.where(Incident.closed_at.is_(None))
    elif status == "closed":
        query = query.where(Incident.closed_at.is_not(None))
    rows, next_cursor = _page(db, query, Incident.opened_at, Incident.id, limit, cursor)
    return IncidentPage(
        items=[IncidentSummary.model_validate(r) for r in rows], next_cursor=next_cursor
    )


@router.get("/incidents/{incident_id}", response_model=IncidentDetail, tags=["incidents"],
            responses={404: {"description": "No such incident"}})
def get_incident(incident_id: uuid.UUID, db: DB) -> IncidentDetail:
    """The full timeline: signals, every proposal with its actions, decisions, executions
    and the measured outcome. Loaded in a fixed number of queries, not one per row."""
    incident = db.scalar(
        select(Incident)
        .where(Incident.id == incident_id)
        .options(
            selectinload(Incident.signals),
            selectinload(Incident.outcomes),
            selectinload(Incident.proposals)
            .selectinload(Proposal.actions)
            .options(selectinload(Action.decision), selectinload(Action.execution)),
        )
    )
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    return IncidentDetail.model_validate(incident)
