"""Savings: proposed against realised (KAV-97, ADR-0065).

    python -m kaval_executor.savings    # a line per removal, then the totals (make savings-report)

Read-only. It reads the proposals the removal planner filed, the person's answer to each, and the
removal worker's own answer (the `removal_done` / `removal_failed` signals), and sorts every
thing Kaval proposed to delete into one of five states:

    waiting     asked, no answer yet
    expired     asked, and nobody answered within a day (the question is dead)
    declined    a person said no
    approved    a person said yes; the worker has not answered, or has not answered "done"
    confirmed   the worker deleted it and said so (`removal_done`)

Only `confirmed` counts as saved. A disk that was approved but not yet confirmed is a hope, and
a hope added into a savings total is how a savings report starts to lie. Backing a disk up and
marking it (step one) saves nothing and is not listed; only the delete steps are.

"Realised" here means "the worker confirmed the thing is gone and named the monthly amount it
stopped costing". It is an estimate priced from the scan's table (Mumbai gp3), not a difference
read off the AWS bill: a nine-cent disk cannot be seen in a bill. The report says so every time.

One thing asked about twice (the question expired, then it was asked again) is one row, in its
furthest state, so the proposed total is not counted twice.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from kaval_agent.removal import ASK_AGAIN_AFTER, STEP_DELETE, STEP_SNAPSHOT
from kaval_shared.models import Action, Decision, Signal, Verdict
from sqlalchemy import select
from sqlalchemy.orm import Session

from kaval_executor.removal import ORDER_STEPS

SOURCE = "aws_ec2"
STATES = ("confirmed", "approved", "waiting", "declined", "expired")
# furthest first: when one thing was asked about twice, the row shown is the furthest one
_RANK = {state: i for i, state in enumerate(STATES)}
DAYS_IN_MONTH = Decimal(30)


@dataclass(frozen=True)
class Row:
    target: str
    step: str
    state: str
    asked_at: datetime
    proposed: Decimal | None  # the monthly saving the proposal promised; None = no price
    realised: Decimal | None  # the monthly saving the worker confirmed; None = none named
    confirmed_at: datetime | None

    def days_saved(self, now: datetime) -> int:
        if self.confirmed_at is None:
            return 0
        return max(0, (now - self.confirmed_at).days)

    def saved_so_far(self, now: datetime) -> Decimal | None:
        if self.realised is None:
            return None
        return self.realised * self.days_saved(now) / DAYS_IN_MONTH


def _amount(raw: object) -> Decimal | None:
    """A money string as a number, or None. Never a guess: bad input is 'no price', not zero."""
    if not isinstance(raw, str):
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    return value if value.is_finite() and value >= 0 else None


def _confirmation(session: Session, action: Action) -> Signal | None:
    """The worker's first `removal_done` for this delete, after the question was asked."""
    prefix = "snapshot/" if action.type == STEP_SNAPSHOT else "ebs/"
    return session.scalars(
        select(Signal)
        .where(
            Signal.source == SOURCE,
            Signal.kind == "removal_done",
            Signal.target == prefix + action.target,
            Signal.observed_at >= action.created_at,
        )
        .order_by(Signal.observed_at)
    ).first()


def _state(
    action: Action, decision: Decision | None, confirmed: Signal | None, now: datetime
) -> str:
    if confirmed is not None and decision is not None and decision.verdict == Verdict.approved:
        return "confirmed"  # a "done" with no yes behind it is never counted
    if decision is None:
        return "expired" if now - action.created_at > ASK_AGAIN_AFTER else "waiting"
    return "approved" if decision.verdict == Verdict.approved else "declined"


def rows(session: Session, now: datetime | None = None) -> list[Row]:
    """One row per thing proposed for deletion, in its furthest state."""
    now = now or datetime.now(UTC)
    actions = session.scalars(
        select(Action)
        .where(Action.type.in_([STEP_DELETE, STEP_SNAPSHOT]))
        .order_by(Action.created_at)
    ).all()
    best: dict[tuple[str, str], Row] = {}
    for action in actions:
        decision = session.scalar(select(Decision).where(Decision.action_id == action.id))
        confirmed = _confirmation(session, action)
        state = _state(action, decision, confirmed, now)
        done = confirmed if state == "confirmed" else None  # only a yes-backed "done" counts
        row = Row(
            target=action.target,
            step=ORDER_STEPS[action.type],
            state=state,
            asked_at=action.created_at,
            proposed=_amount(action.params.get("saving_usd_month")),
            realised=_amount(done.value.get("monthly_saving_usd")) if done else None,
            confirmed_at=done.observed_at if done else None,
        )
        key = (action.type, action.target)
        if key not in best or _RANK[state] < _RANK[best[key].state]:
            best[key] = row
    return sorted(best.values(), key=lambda r: r.asked_at)


@dataclass(frozen=True)
class Totals:
    counts: dict[str, int]
    proposed: Decimal  # everything priced, in any state: what was on the table
    approved: Decimal  # priced, said yes to (confirmed or not yet)
    realised: Decimal  # priced, confirmed: the monthly amount that stopped
    saved_so_far: Decimal  # realised, times the days since each was confirmed
    unpriced: int  # rows with no price: counted in `counts`, absent from every amount


def totals(items: Sequence[Row], now: datetime) -> Totals:
    counts = {s: sum(1 for r in items if r.state == s) for s in STATES}
    proposed = sum((r.proposed for r in items if r.proposed is not None), Decimal(0))
    said_yes = ("approved", "confirmed")
    approved = sum(
        (r.proposed for r in items if r.proposed is not None and r.state in said_yes), Decimal(0)
    )
    realised = sum((r.realised for r in items if r.realised is not None), Decimal(0))
    so_far = sum(
        (s for r in items if (s := r.saved_so_far(now)) is not None), Decimal(0)
    )
    unpriced = sum(1 for r in items if r.proposed is None and r.realised is None)
    return Totals(counts, proposed, approved, realised, so_far, unpriced)


def _usd(value: Decimal | None) -> str:
    return "     n/a" if value is None else f"${value:>6.2f}"


def render(items: Sequence[Row], now: datetime) -> list[str]:
    if not items:
        return ["nothing has been proposed for deletion yet"]
    lines = [f"{'thing':24} {'state':10} {'promised':>8} {'confirmed':>9} {'days':>5}  asked"]
    for r in items:
        lines.append(
            f"{r.target[:24]:24} {r.state:10} {_usd(r.proposed):>8} {_usd(r.realised):>9} "
            f"{r.days_saved(now):>5}  {r.asked_at:%Y-%m-%d %H:%M}"
        )
    t = totals(items, now)
    lines.append("")
    lines.append("  ".join(f"{s} {t.counts[s]}" for s in STATES))
    lines.append(f"promised (everything priced)   ${t.proposed:.2f} a month")
    lines.append(f"approved by a person           ${t.approved:.2f} a month")
    lines.append(f"confirmed gone (realised)      ${t.realised:.2f} a month")
    lines.append(f"saved so far, estimated        ${t.saved_so_far:.2f}")
    if t.unpriced:
        lines.append(
            f"{t.unpriced} thing(s) carry no price (backup copies): in the counts, not the amounts"
        )
    lines.append(
        "Estimates priced from the scan's table, counted only once the worker confirmed the "
        "deletion. Not read off the AWS bill: a saving this small cannot be seen there."
    )
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    from kaval_shared.db import get_engine  # only a real run needs the database

    now = datetime.now(UTC)
    with Session(get_engine()) as session:
        print("\n".join(render(rows(session, now), now)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
