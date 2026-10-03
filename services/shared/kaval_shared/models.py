"""The append-only spine: signal -> incident -> proposal -> action -> decision -> execution
-> outcome.

Nothing here is ever UPDATEd in place except `incident.closed_at` (an incident transitions
from open to closed) and `execution.finished_at` (set once the run completes). Every other
row is written once. That is what makes this table set the audit trail rather than just a
database — see docs/architecture/overview.md#data-model.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# The dimension of every stored embedding (KAV-40): all-minilm's output size. Defined here,
# next to the column that uses it, so kaval_agent.embeddings imports it rather than repeating
# the number — a mismatch would only be caught by Postgres refusing the INSERT.
RUNBOOK_EMBED_DIM = 384


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Severity(enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class RiskLevel(enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"


class BlastRadius(enum.Enum):
    pod = "pod"
    deployment = "deployment"
    namespace = "namespace"
    node = "node"
    account = "account"


class PolicyClass(enum.Enum):
    auto = "auto"
    ask = "ask"
    never = "never"


class Verdict(enum.Enum):
    approved = "approved"
    denied = "denied"
    auto_approved = "auto_approved"


class ExecutionStatus(enum.Enum):
    success = "success"
    failed = "failed"
    skipped = "skipped"


class Signal(Base):
    """One raw observation, exactly as collected. Never correlated or interpreted here."""

    __tablename__ = "signal"

    id: Mapped[uuid.UUID] = _uuid_pk()
    source: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    target: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_signal_source_kind_observed_at", "source", "kind", "observed_at"),
        Index("ix_signal_target", "target"),
    )


class IncidentSignal(Base):
    """Which raw signals were correlated into which incident. Many-to-many, insert-only."""

    __tablename__ = "incident_signal"

    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incident.id"), primary_key=True
    )
    signal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("signal.id"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # The primary key leads with incident_id, so it can't answer "is this signal in an
    # incident yet?", the question correlation asks of every new signal (KAV-39).
    __table_args__ = (Index("ix_incident_signal_signal_id", "signal_id"),)


class Incident(Base):
    """A correlated group of signals the agent has decided to treat as one problem."""

    __tablename__ = "incident"

    id: Mapped[uuid.UUID] = _uuid_pk()
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[Severity] = mapped_column(
        SAEnum(Severity, name="severity"), nullable=False
    )
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    signals: Mapped[list[Signal]] = relationship(secondary="incident_signal")
    proposals: Mapped[list[Proposal]] = relationship(back_populates="incident")
    outcomes: Mapped[list[Outcome]] = relationship(back_populates="incident")

    __table_args__ = (Index("ix_incident_fingerprint", "fingerprint"),)


class Proposal(Base):
    """What the LLM recommended for an incident, and what that cost."""

    __tablename__ = "proposal"

    id: Mapped[uuid.UUID] = _uuid_pk()
    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incident.id"), nullable=False, index=True
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    root_cause: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(nullable=False)
    risk: Mapped[RiskLevel] = mapped_column(SAEnum(RiskLevel, name="risk_level"), nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    tokens_in: Mapped[int] = mapped_column(nullable=False)
    tokens_out: Mapped[int] = mapped_column(nullable=False)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    incident: Mapped[Incident] = relationship(back_populates="proposals")
    actions: Mapped[list[Action]] = relationship(back_populates="proposal")

    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_proposal_confidence_range"),
    )


class Action(Base):
    """One concrete operation the executor could perform, still unclassified until policy runs."""

    __tablename__ = "action"

    id: Mapped[uuid.UUID] = _uuid_pk()
    proposal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("proposal.id"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    target: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    reversible: Mapped[bool] = mapped_column(nullable=False)
    blast_radius: Mapped[BlastRadius] = mapped_column(
        SAEnum(BlastRadius, name="blast_radius"), nullable=False
    )
    policy_class: Mapped[PolicyClass] = mapped_column(
        SAEnum(PolicyClass, name="policy_class"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Set once a Slack ChatOps notification has actually been posted for this action
    # (KAV-55). Lets the notifier poller ask "which ask-class actions have I not yet told
    # anyone about" directly in SQL, instead of re-deriving it from the decision table (an
    # action can be undecided and already notified, which decision.is_(None) alone can't
    # distinguish).
    slack_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    proposal: Mapped[Proposal] = relationship(back_populates="actions")
    decision: Mapped[Decision | None] = relationship(back_populates="action")
    execution: Mapped[Execution | None] = relationship(back_populates="action")


class Decision(Base):
    """The human (or the `auto` policy class) verdict on one action. One per action."""

    __tablename__ = "decision"

    id: Mapped[uuid.UUID] = _uuid_pk()
    action_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("action.id"), nullable=False
    )
    verdict: Mapped[Verdict] = mapped_column(SAEnum(Verdict, name="verdict"), nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    action: Mapped[Action] = relationship(back_populates="decision")

    __table_args__ = (UniqueConstraint("action_id", name="uq_decision_action_id"),)


class Execution(Base):
    """What actually ran for an approved action, and its before/after state. One per action.

    `stdout` must already be redacted by the executor before this row is written — command
    output can contain secret values (e.g. `kubectl get secret -o yaml`), and this table is
    read by the gateway and displayed on the phone. Redact at write time, not later; see
    docs/adr/0005-data-durability-and-staging-seeding.md.
    """

    __tablename__ = "execution"

    id: Mapped[uuid.UUID] = _uuid_pk()
    action_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("action.id"), nullable=False
    )
    status: Mapped[ExecutionStatus] = mapped_column(
        SAEnum(ExecutionStatus, name="execution_status"), nullable=False
    )
    stdout: Mapped[str | None] = mapped_column(Text)
    before_state: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    after_state: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    action: Mapped[Action] = relationship(back_populates="execution")

    __table_args__ = (UniqueConstraint("action_id", name="uq_execution_action_id"),)


class Outcome(Base):
    """Measured 5 minutes after execution: did it actually resolve the incident."""

    __tablename__ = "outcome"

    id: Mapped[uuid.UUID] = _uuid_pk()
    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incident.id"), nullable=False, index=True
    )
    resolved: Mapped[bool] = mapped_column(nullable=False)
    mttr_sec: Mapped[int | None] = mapped_column()
    regression: Mapped[bool] = mapped_column(nullable=False)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    incident: Mapped[Incident] = relationship(back_populates="outcomes")


class RunbookChunk(Base):
    """One H2 section of a runbook in `docs/runbooks/`, with its embedding (KAV-40, ADR-0015).

    Not part of the append-only spine above: `docs/runbooks/` is the source of truth, this
    table is its index, and `kaval_agent.index_runbooks` re-syncs it — creating, updating or
    deleting rows — whenever a runbook changes. Nothing else writes here.
    """

    __tablename__ = "runbook_chunk"

    id: Mapped[uuid.UUID] = _uuid_pk()
    path: Mapped[str] = mapped_column(Text, nullable=False)  # e.g. "restore-from-backup.md"
    heading: Mapped[str] = mapped_column(Text, nullable=False)  # e.g. "Likely causes"
    ordinal: Mapped[int] = mapped_column(nullable=False)  # position within the file
    content: Mapped[str] = mapped_column(Text, nullable=False)  # title + heading + body
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)  # sha256(content)
    embedding: Mapped[list[float]] = mapped_column(Vector(RUNBOOK_EMBED_DIM), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (UniqueConstraint("path", "heading", name="uq_runbook_chunk_path_heading"),)
