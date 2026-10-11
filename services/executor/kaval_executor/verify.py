"""Did the fix hold? Writes the `outcome` row for each executed fix (KAV-77, ADR-0041).

Until now nothing wrote the `outcome` table, so "recovery time" had no end point. Five minutes
after an action succeeded, this looks at what the system itself reports for the same workload:

- resolved: no new problem signal for that workload in the five minutes after the fix finished.
  Silence is the evidence, the same evidence the correlator uses to close an incident.
- not resolved: the problem was still being reported. The row says so; nothing retries it.
- regression: an earlier fix was judged resolved, and the same fingerprint opened a new
  incident within two hours. Recorded as a second row for the first incident, so a fix that
  looked good for ten minutes is not counted as a success forever.

`mttr_sec` is wall-clock: the incident's first signal to the moment the fix finished, human
approval included. `kaval_executor.mttr` splits it into stages and takes the human wait out.

Only Kubernetes incidents are judged here. AWS cost incidents are daily data about a day that
is already over, so five minutes proves nothing; Phase 6 gives them their own check.

    python -m kaval_executor.verify         # one pass, normally run inside the executor's loop

It runs in the executor's process only because that role already holds INSERT on `outcome`
(ADR-0041 records the alternative, a separate verifier with its own role, and when to switch).
It never touches the cluster and a failure here never stops an execution.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from kaval_agent.correlate import DOMAINS, cause_of, subject
from kaval_shared.models import (
    Action,
    Execution,
    ExecutionStatus,
    Incident,
    IncidentSignal,
    Outcome,
    Proposal,
    Signal,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

VERIFY_AFTER = timedelta(minutes=5)
REGRESSION_WINDOW = timedelta(hours=2)
_K8S_SOURCES = [source for source, domain in DOMAINS.items() if domain == "k8s"]


@dataclass(frozen=True)
class Verified:
    incident_id: object
    fingerprint: str
    resolved: bool
    regression: bool
    mttr_sec: int | None


def _k8s_subject(fingerprint: str) -> str | None:
    """`oom_killed:k8s:kaval-demo/checkout` -> `kaval-demo/checkout`; None for anything else."""
    parts = fingerprint.split(":", 2)
    return parts[2] if len(parts) == 3 and parts[1] == "k8s" else None


def _last_fix(session: Session) -> dict[object, tuple[Incident, datetime]]:
    """Each incident with a successful execution, and when its latest one finished."""
    rows = session.execute(
        select(Incident, func.max(Execution.finished_at))
        .join(Proposal, Proposal.incident_id == Incident.id)
        .join(Action, Action.proposal_id == Proposal.id)
        .join(Execution, Execution.action_id == Action.id)
        .where(Execution.status == ExecutionStatus.success, Execution.finished_at.is_not(None))
        .group_by(Incident.id)
    ).all()
    return {incident.id: (incident, finished) for incident, finished in rows if finished}


def _judged_at(session: Session, incident_id: object) -> datetime | None:
    return session.scalar(select(func.max(Outcome.measured_at))
                          .where(Outcome.incident_id == incident_id))


def _still_reported(session: Session, where: str, start: datetime, end: datetime) -> bool:
    signals = session.scalars(
        select(Signal).where(Signal.source.in_(_K8S_SOURCES),
                             Signal.observed_at > start, Signal.observed_at <= end,
                             Signal.target.like(where.split("/", 1)[0] + "/%"))
    ).all()
    return any(subject(s) == where and cause_of(s, set()) is not None for s in signals)


def _first_signal(session: Session, incident_id: object) -> datetime | None:
    return session.scalar(select(func.min(Signal.observed_at))
                          .join(IncidentSignal, IncidentSignal.signal_id == Signal.id)
                          .where(IncidentSignal.incident_id == incident_id))


def verify_pending(session: Session, now: datetime) -> list[Verified]:
    done: list[Verified] = []
    for incident, finished in _last_fix(session).values():
        where = _k8s_subject(incident.fingerprint)
        if where is None or finished + VERIFY_AFTER > now:
            continue
        judged = _judged_at(session, incident.id)
        if judged is not None and judged >= finished:
            continue
        resolved = not _still_reported(session, where, finished, finished + VERIFY_AFTER)
        first = _first_signal(session, incident.id)
        mttr = max(0, int((finished - first).total_seconds())) if resolved and first else None
        session.add(Outcome(incident_id=incident.id, resolved=resolved, mttr_sec=mttr,
                            regression=False, measured_at=now))
        done.append(Verified(incident.id, incident.fingerprint, resolved, False, mttr))
    session.commit()
    return done


def flag_regressions(session: Session, now: datetime) -> list[Verified]:
    """A fix judged resolved whose fingerprint came back within REGRESSION_WINDOW."""
    done: list[Verified] = []
    resolved = session.scalars(
        select(Outcome).where(Outcome.resolved.is_(True), Outcome.regression.is_(False),
                              Outcome.measured_at > now - REGRESSION_WINDOW)
    ).all()
    for outcome in resolved:
        incident = session.get(Incident, outcome.incident_id)
        if incident is None:
            continue
        already = session.scalar(select(func.count()).select_from(Outcome).where(
            Outcome.incident_id == incident.id, Outcome.regression.is_(True)))
        came_back = session.scalar(select(func.count()).select_from(Incident).where(
            Incident.fingerprint == incident.fingerprint, Incident.id != incident.id,
            Incident.opened_at > outcome.measured_at,
            Incident.opened_at <= outcome.measured_at + REGRESSION_WINDOW))
        if already or not came_back:
            continue
        session.add(Outcome(incident_id=incident.id, resolved=False, mttr_sec=None,
                            regression=True, measured_at=now))
        done.append(Verified(incident.id, incident.fingerprint, False, True, None))
    session.commit()
    return done


def run_once(session: Session, now: datetime | None = None) -> list[Verified]:
    now = now or datetime.now(UTC)
    return verify_pending(session, now) + flag_regressions(session, now)


def report(results: Sequence[Verified]) -> list[str]:
    if not results:
        return ["nothing to verify"]
    return [
        f"{'REGRESSED' if r.regression else 'resolved' if r.resolved else 'NOT RESOLVED':12} "
        f"{r.fingerprint}" + (f"  {r.mttr_sec}s" if r.mttr_sec is not None else "")
        for r in results
    ]


def main() -> int:
    from kaval_shared.db import get_engine  # only a real run needs the database
    with Session(get_engine()) as session:
        for line in report(run_once(session)):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
