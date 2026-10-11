"""Time to restore, by stage, with the human wait taken out (KAV-77, ADR-0041).

    python -m kaval_executor.mttr            # every incident whose fix held (make mttr)
    python -m kaval_executor.mttr --last 20  # only the 20 most recent

One incident's wall-clock time to restore splits into stages that add up exactly:

    detect    first signal -> incident opened      (includes the 60 s the correlator waits)
    diagnose  incident opened -> proposal filed
    human     proposal filed -> decision           (0 when policy auto-approved it)
    queue     decision (or proposal) -> execution started
    execute   execution started -> finished

`wall` is that sum, the same number `verify.py` stores as `outcome.mttr_sec`; `machine` is
`wall` minus `human`, the part Kaval controls. Both are always reported: quoting only the
flattering one would be dishonest, and the human stage usually dominates. The summary gives
median, p90 and worst, not the mean, because incident durations are skewed.
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from kaval_shared.models import (
    Action,
    Decision,
    Execution,
    ExecutionStatus,
    Incident,
    IncidentSignal,
    Outcome,
    Proposal,
    Signal,
    Verdict,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

STAGES = ("detect", "diagnose", "human", "queue", "execute")


@dataclass(frozen=True)
class Timing:
    fingerprint: str
    detect: float
    diagnose: float
    human: float
    queue: float
    execute: float

    @property
    def wall(self) -> float:
        return self.detect + self.diagnose + self.human + self.queue + self.execute

    @property
    def machine(self) -> float:
        return self.wall - self.human


def _secs(later: datetime, earlier: datetime) -> float:
    return max(0.0, (later - earlier).total_seconds())


def percentile(values: Sequence[float], p: float) -> float:
    """Nearest-rank percentile: honest on the handful of samples a game day produces."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p * len(ordered)) - 1)]


def timings(session: Session, last: int | None = None) -> list[Timing]:
    """One row per incident that has a resolved outcome and a successful execution."""
    incidents = session.scalars(
        select(Incident).join(Outcome, Outcome.incident_id == Incident.id)
        .where(Outcome.resolved.is_(True)).group_by(Incident.id)
        .order_by(func.max(Outcome.measured_at).desc()).limit(last)
    ).all()
    rows: list[Timing] = []
    for incident in incidents:
        found = session.execute(
            select(Proposal, Action, Execution)
            .join(Action, Action.proposal_id == Proposal.id)
            .join(Execution, Execution.action_id == Action.id)
            .where(Proposal.incident_id == incident.id,
                   Execution.status == ExecutionStatus.success, Execution.finished_at.is_not(None))
            .order_by(Execution.finished_at.desc()).limit(1)
        ).first()
        first = session.scalar(select(func.min(Signal.observed_at))
                               .join(IncidentSignal, IncidentSignal.signal_id == Signal.id)
                               .where(IncidentSignal.incident_id == incident.id))
        if found is None or first is None:
            continue
        proposal, action, execution = found
        decision = session.scalar(select(Decision).where(Decision.action_id == action.id))
        filed = proposal.created_at
        waited = decision is not None and decision.verdict == Verdict.approved
        human = _secs(decision.decided_at, filed) if waited and decision else 0.0
        ready = decision.decided_at if waited and decision else filed
        assert execution.finished_at is not None
        rows.append(Timing(
            incident.fingerprint,
            detect=_secs(incident.opened_at, first),
            diagnose=_secs(filed, incident.opened_at),
            human=human,
            queue=_secs(execution.started_at, ready),
            execute=_secs(execution.finished_at, execution.started_at),
        ))
    return rows


def _fmt(seconds: float) -> str:
    return f"{seconds:6.0f}s"


def render(rows: Sequence[Timing]) -> list[str]:
    if not rows:
        return ["no resolved incidents yet"]
    lines = [f"{'incident':44} " + " ".join(f"{s:>7}" for s in STAGES) + "    wall machine"]
    for r in rows:
        lines.append(f"{r.fingerprint[:44]:44} " + " ".join(
            _fmt(getattr(r, s)) for s in STAGES) + f" {_fmt(r.wall)} {_fmt(r.machine)}")
    lines.append("")
    lines.append(f"{len(rows)} incident(s)       median      p90    worst")
    for label, values in (("machine", [r.machine for r in rows]),
                          ("wall-clock", [r.wall for r in rows])):
        lines.append(f"{label:16} {_fmt(statistics.median(values))} {_fmt(percentile(values, 0.9))}"
                     f" {_fmt(max(values))}")
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m kaval_executor.mttr",
                                     description="Time to restore, by stage.")
    parser.add_argument("--last", type=int, metavar="N", help="only the N most recent incidents")
    args = parser.parse_args(argv)
    from kaval_shared.db import get_engine  # only a real run needs the database
    with Session(get_engine()) as session:
        print("\n".join(render(timings(session, args.last))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
