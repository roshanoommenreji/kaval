"""The /v1 REST surface: read-only views of signals and incidents (KAV-23), plus the one
write the gateway makes — a human's approve/deny becoming a `decision` row (KAV-47).

Everything except `decide()` stays read-only, and `read_session` makes the database enforce
that, not just the code. `decide()` is the sole exception, on its own `write_session` — see
that function's docstring for why a human's verdict landing in `decision` is not the same
kind of risk as a read endpoint gaining a write.

Lists are newest first and paged with an opaque cursor, not `?page=N`. Signals arrive
continuously, so offset paging would skip or repeat rows as new ones land between two
requests; a cursor that says "older than this row" can't.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Annotated, Literal, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query
from kaval_shared.db import get_engine
from kaval_shared.models import Action, Decision, Incident, PolicyClass, Proposal, Signal, Verdict
from sqlalchemy import Select, literal, select, text, tuple_
from sqlalchemy.orm import InstrumentedAttribute, Session, selectinload

from kaval_gateway.schemas import (
    DecisionIn,
    DecisionOut,
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


def write_session() -> Generator[Session, None, None]:
    """One session per request, ordinary read-write.

    This is the gateway's only writable path. It is not a weaker guarantee than
    `read_session`'s database-enforced READ ONLY, just a differently-shaped one: there is
    exactly one statement this session is ever asked to run (`INSERT INTO decision`, via
    `decide()` below), the row it writes is itself an audit record, and `action.decision`'s
    `UniqueConstraint` (one decision per action) means even a buggy caller can't overwrite
    an earlier verdict — only ever insert a brand new, append-only one. The actual safety
    boundary a `decision` row crosses is not "can the gateway write to Postgres" but "can an
    approval make the executor do something it shouldn't" — and that boundary is enforced a
    layer down, by the executor's own independent policy re-check immediately before it
    acts (`kaval_executor.executor`, `policy/README.md`'s "evaluated twice").
    """
    with Session(get_engine()) as session:
        yield session


DB = Annotated[Session, Depends(read_session)]
WriteDB = Annotated[Session, Depends(write_session)]
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
    """Correlated problems, newest first, written by the agent's correlator (KAV-39)."""
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


@router.post(
    "/actions/{action_id}/decisions", response_model=DecisionOut, status_code=201,
    tags=["actions"],
    responses={
        404: {"description": "No such action"},
        409: {"description": "Already decided, or policy class 'never'"},
    },
)
def decide(action_id: uuid.UUID, body: DecisionIn, db: WriteDB) -> DecisionOut:
    """Record a human's approve or deny on one proposed action (KAV-47). There is no
    corresponding GET-then-act race to worry about: `action.decision`'s `UniqueConstraint`
    means a second decision on the same action fails at the database regardless of what
    this function checks first — the checks below exist to fail with a clear 409 instead of
    an unhandled `IntegrityError`, not to be the only thing preventing a double-decision.

    `policy_class == never` is refused here too, before the database even gets asked,
    because ADR-0006's rule 1 means "never" regardless of what a human says, not just
    regardless of what the model's confidence says. This is a convenience rejection, not
    the enforcement boundary: even if this check were removed, or this whole endpoint were
    compromised and wrote an `approved` decision directly, the executor's own independent
    policy re-check (`kaval_executor.executor`) evaluates the identical `never` rule again,
    from the action's real fields, immediately before it would act — and refuses it there
    too. Two independent refusals, not one checked twice.
    """
    action = db.get(Action, action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="action not found")
    if action.policy_class == PolicyClass.never:
        raise HTTPException(
            status_code=409,
            detail="action is policy class 'never' — it cannot be approved or denied",
        )
    if action.decision is not None:
        raise HTTPException(status_code=409, detail="action already has a decision")
    decision = Decision(
        action_id=action.id,
        verdict=Verdict(body.verdict),
        actor=body.actor,
        reason=body.reason,
        decided_at=datetime.now(UTC),
    )
    db.add(decision)
    db.commit()
    db.refresh(decision)
    return DecisionOut.model_validate(decision)
