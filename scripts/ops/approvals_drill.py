"""Staging proof for approval expiry and escalation (KAV-82, ADR-0048).

Staging has no Slack and no language model, so this stands in for both, and says so:

  DRILL_MODE=file    (AGENT's database role, the one allowed to write proposals)
      Files one harmless approval request by hand: an `ask`-class memory-limit change aimed at
      a workload that does not exist, so approving it could only ever be recorded as "nothing
      to do". Prints its ACTION id and the real policy engine's class for it.

  DRILL_MODE=report  (GATEWAY's database role, read-only here)
      For each such request: its age, whether it has expired, and the exact messages the REAL
      notifier would send right now (reminder, escalation or expiry), produced by running
      `approvals.notify_pass` with a recording client inside a transaction that is rolled back.
      Nothing is posted and nothing is stored. The timings are read from the environment the
      chaos wrapper copies from the running gateway, so this shows what staging really uses.

The live part is the rest: after the request's expiry, `make chaos-approve` on it must be refused
by the real gateway (HTTP 409, "expired").
"""

from __future__ import annotations

import os
import sys
import textwrap
from datetime import UTC, datetime
from decimal import Decimal

DRILL = "drill_approval"
TARGET = "approval-drill-nothing"
CONFIDENCE = 0.9


class _Recorder:
    """Stands in for Slack's client: keeps what would have been posted, sends nothing."""

    def __init__(self) -> None:
        self.posted: list[tuple[str, str]] = []

    def chat_postMessage(self, **kwargs: object) -> dict[str, object]:  # noqa: N802
        self.posted.append((str(kwargs["channel"]), str(kwargs["text"])))
        return {"ok": True, "ts": "dry-run"}

    def chat_update(self, **kwargs: object) -> dict[str, object]:
        return {"ok": True}


def file_request() -> int:
    from kaval_agent import policy
    from kaval_shared.db import get_engine
    from kaval_shared.models import (
        Action,
        BlastRadius,
        Incident,
        Proposal,
        RiskLevel,
        Severity,
    )
    from sqlalchemy.orm import Session

    namespace = os.environ["CHAOS_TARGET_NAMESPACE"]
    klass = policy.classify("increase_memory_limit", BlastRadius.deployment, True, CONFIDENCE,
                            cause=DRILL)
    with Session(get_engine()) as db:
        stamp = datetime.now(UTC)
        incident = Incident(fingerprint=f"{DRILL}:k8s:{namespace}/{TARGET}:{stamp:%H%M%S}",
                            severity=Severity.low, opened_at=stamp)
        proposal = Proposal(
            incident=incident, summary="approval drill: raise a limit on a workload that does "
            "not exist (filed by hand; staging has no language model)",
            root_cause="a rehearsal of an unanswered approval", confidence=CONFIDENCE,
            risk=RiskLevel.low, model="by-hand", tokens_in=0, tokens_out=0, cost_usd=Decimal("0"),
        )
        action = Action(proposal=proposal, type="increase_memory_limit",
                        target=f"{namespace}/{TARGET}", params={}, reversible=True,
                        blast_radius=BlastRadius.deployment, policy_class=klass)
        db.add_all([incident, proposal, action])
        db.commit()
        print(f"policy class: {klass.value}")
        print(f"ACTION {action.id}")
    return 0 if klass.value == "ask" else 1


def report() -> int:
    from kaval_gateway import approvals
    from kaval_shared.db import get_engine
    from kaval_shared.models import Action
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    timing = approvals.Timing.from_env()
    now = datetime.now(UTC)
    print(f"timings in use: reminder {timing.reminder}, escalate {timing.escalate}, "
          f"expire {timing.expire}")
    engine = get_engine()
    with engine.connect() as conn:
        savepoint = conn.begin_nested()
        try:
            with Session(bind=conn, join_transaction_mode="create_savepoint") as db:
                for action in db.scalars(select(Action).where(Action.target.endswith(TARGET))
                                         .order_by(Action.created_at)):
                    age = now - action.created_at
                    state = ("decided" if action.decision is not None else
                             "EXPIRED" if approvals.is_expired(action, now, timing) else "waiting")
                    print(f"request {action.id}  age {int(age.total_seconds() // 60)} min  {state}")
                recorder = _Recorder()
                approvals.notify_pass(db, recorder, "dry-run", "dry-run-escalation", now=now,
                                      timing=timing, told_only=False)
        finally:
            savepoint.rollback()
        conn.rollback()
    if not recorder.posted:
        print("the real notifier would send nothing right now")
    for channel, text in recorder.posted:
        print(f"the real notifier would post to {channel}:")
        print(textwrap.indent(text, "    "))
    return 0


def main() -> int:
    mode = os.environ.get("DRILL_MODE", "")
    if mode == "file":
        return file_request()
    if mode == "report":
        return report()
    print("DRILL_MODE must be 'file' or 'report'")
    return 2


if __name__ == "__main__":
    sys.exit(main())
