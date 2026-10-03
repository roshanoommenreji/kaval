"""The one write every approval surface shares: a human's approve/deny becoming a `decision`
row (`KAV-47`). `api.decide()` (the HTTP endpoint) and `slack_chatops` (`KAV-55`) both call
`record_decision()` so there is exactly one place this check is written, not one per surface
that could quietly drift apart.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum

from kaval_shared.models import Action, Decision, PolicyClass, Verdict
from sqlalchemy.orm import Session


class DecisionOutcome(Enum):
    recorded = "recorded"
    not_found = "not_found"
    never_class = "never_class"
    already_decided = "already_decided"


def record_decision(
    session: Session, action_id: uuid.UUID, verdict: Verdict, actor: str,
    reason: str | None = None,
) -> tuple[DecisionOutcome, Decision | None]:
    """`policy_class == never` is refused here regardless of what a human says (ADR-0006
    rule 1), and a second decision on an already-decided action is refused too — though
    `action.decision`'s `UniqueConstraint` would catch that race at the database either way.
    Neither refusal is the real enforcement boundary: the executor re-checks policy
    independently from the action's own fields immediately before it acts
    (`kaval_executor.executor`), regardless of what this function ever wrote.
    """
    action = session.get(Action, action_id)
    if action is None:
        return DecisionOutcome.not_found, None
    if action.policy_class == PolicyClass.never:
        return DecisionOutcome.never_class, None
    if action.decision is not None:
        return DecisionOutcome.already_decided, None
    decision = Decision(
        action_id=action.id, verdict=verdict, actor=actor, reason=reason,
        decided_at=datetime.now(UTC),
    )
    session.add(decision)
    session.commit()
    session.refresh(decision)
    return DecisionOutcome.recorded, decision
