"""What the API returns: Pydantic models read straight off the ORM rows (`from_attributes`).

These are the gateway's contract with the phone, published as OpenAPI at /openapi.json.
They are deliberately separate from the ORM models: the table can gain a column without
the API changing, and the API can't leak a column by accident.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from kaval_shared.models import (
    BlastRadius,
    ExecutionStatus,
    PolicyClass,
    RiskLevel,
    Severity,
    Verdict,
)
from pydantic import BaseModel, ConfigDict, Field, computed_field


class _Row(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class SignalOut(_Row):
    id: uuid.UUID
    source: str = Field(examples=["kubernetes"])
    kind: str = Field(examples=["pod_oom_killed"])
    target: str = Field(examples=["kaval-demo/checkout-7f9c4-x2klp"])
    value: dict[str, object]
    observed_at: datetime
    created_at: datetime


class DecisionOut(_Row):
    id: uuid.UUID
    verdict: Verdict
    actor: str
    reason: str | None
    decided_at: datetime


class DecisionIn(BaseModel):
    """The body of `POST /v1/actions/{action_id}/decisions` — a human's approve or deny
    (KAV-47). No `auto_approved` here: that verdict is written only by the policy engine
    itself, at proposal time, never by a human-facing endpoint — see `Verdict` in
    `kaval_shared.models`. `actor` is free text for now (a name, or `cli` from the Phase-3
    approve script) or `slack:<username>` from Slack ChatOps (`KAV-55`,
    `kaval_gateway.decisions.record_decision`). Cognito identity would replace it if the
    deferred mobile app (Phase 9) is ever built."""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["approved", "denied"]
    actor: str = Field(min_length=1, examples=["roshan", "cli"])
    reason: str | None = Field(default=None, max_length=2000)


class ExecutionOut(_Row):
    id: uuid.UUID
    status: ExecutionStatus
    stdout: str | None = Field(description="Already redacted by the executor at write time")
    before_state: dict[str, object] | None
    after_state: dict[str, object] | None
    started_at: datetime
    finished_at: datetime | None


class ActionOut(_Row):
    id: uuid.UUID
    type: str
    target: str
    params: dict[str, object]
    reversible: bool
    blast_radius: BlastRadius
    policy_class: PolicyClass
    decision: DecisionOut | None
    execution: ExecutionOut | None


class ProposalOut(_Row):
    id: uuid.UUID
    summary: str
    root_cause: str
    confidence: float
    risk: RiskLevel
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: Decimal
    created_at: datetime
    actions: list[ActionOut]


class OutcomeOut(_Row):
    id: uuid.UUID
    resolved: bool
    mttr_sec: int | None
    regression: bool
    measured_at: datetime


class IncidentSummary(_Row):
    id: uuid.UUID
    fingerprint: str
    severity: Severity
    opened_at: datetime
    closed_at: datetime | None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def status(self) -> Literal["open", "closed"]:
        return "open" if self.closed_at is None else "closed"


class IncidentDetail(IncidentSummary):
    """One incident and everything that happened to it: the timeline the phone shows."""

    signals: list[SignalOut]
    proposals: list[ProposalOut]
    outcomes: list[OutcomeOut]


_NEXT_CURSOR = "Pass as ?cursor= for the next page; null at the end"


class SignalPage(BaseModel):
    items: list[SignalOut]
    next_cursor: str | None = Field(description=_NEXT_CURSOR)


class IncidentPage(BaseModel):
    items: list[IncidentSummary]
    next_cursor: str | None = Field(description=_NEXT_CURSOR)
