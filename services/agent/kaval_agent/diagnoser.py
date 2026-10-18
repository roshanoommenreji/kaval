"""The diagnosis worker: asks the model about each new incident by itself (KAV-87, ADR-0055).

Until now nothing in the cluster called the model: the correlator groups signals into incidents,
and a person ran `make diagnose` to get a proposal. This is the loop that does it unattended.

    python -m kaval_agent.diagnoser --every 30     # what the diagnoser Deployment runs
    python -m kaval_agent.diagnoser                # one pass (make diagnoser-once)

It is a **separate process from the correlator on purpose**: a diagnosis can take a minute, and
the model may be down. If this loop is slow or stuck, incidents are still found and the
executor still acts on what was already approved. "The model failed" never stops anything else.

One pass, in one transaction, under an advisory lock (so a second copy does nothing):

1. Pick the **oldest** incident that is open, recent, not for a person, and has no proposal.
2. Build its context, ask the model (`diagnose.diagnose`), write the proposal.

The rules that keep it safe and quiet:

* **One diagnosis per incident, ever.** A proposal row (even one with no actions) is the record
  that it was diagnosed; the picker skips any incident that has one. A failed attempt writes
  nothing, so it is tried again later.
* **Nothing for a person's incidents.** `kaval_*` health incidents, a full disk and a slow
  service are skipped here, and `write_proposal` refuses them again.
* **Not the old ones.** An incident older than `DIAGNOSE_MAX_AGE_HOURS` is left alone: an
  approval expires after four hours (ADR-0048), and a diagnosis of how things were yesterday is
  worth less than a person looking.
* **A speed limit.** At most `DIAGNOSE_MAX_PER_HOUR` proposals an hour, so one flapping workload
  cannot keep the processor busy or fill Slack. Incidents over the limit wait, and the
  "waiting" alert tells a person.
* **When the model fails, stop the pass and back off** (30 s, doubling to 10 min). The
  incident stays open and undiagnosed; `kaval_diagnosis_*` and the loop-stalled alert say so.
* **It can only write proposals.** No cluster or AWS credentials are mounted. A proposal still
  needs policy and, almost always, a person's approval.

**What it exports on :9100** (no ids, targets or error text: the network cannot hide a port
from other pods, see `kaval_shared.metrics`): `kaval_diagnosis_total{outcome}`,
`kaval_diagnosis_waiting`, `kaval_diagnosis_oldest_wait_seconds`, `kaval_diagnosis_last_seconds`,
`kaval_diagnosis_confidence_limited_total` (proposals whose stored confidence was lowered because
the incident held thin evidence, KAV-88: a count, so it is visible when the rule starts or stops
biting, and no data in it).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
from kaval_shared import health, metrics
from kaval_shared.models import Incident, Proposal
from prometheus_client import Counter, Gauge
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from kaval_agent import __version__, removal
from kaval_agent.context import Context, build_context
from kaval_agent.diagnose import DiagnosisError, ModelUsage, diagnose, write_proposal
from kaval_agent.embeddings import EmbeddingError
from kaval_agent.schema import Diagnosis

SERVICE = "diagnoser"
LOCK_KEY = 0x4B41_5644  # "KAVD"; the correlator's key is different, so they never block each other

DEFAULT_MAX_AGE_HOURS = 4.0
DEFAULT_MAX_PER_HOUR = 6
DEFAULT_TIMEOUT_SECONDS = 90.0
BACKOFF_FIRST = timedelta(seconds=30)
BACKOFF_MAX = timedelta(minutes=10)
CANDIDATES = 50  # how many waiting incidents one pass looks at; it diagnoses one

_DIAGNOSES = Counter("kaval_diagnosis", "Diagnosis attempts by outcome", ["outcome"])
_LIMITED = Counter("kaval_diagnosis_confidence_limited",
                   "Proposals stored with a lower confidence than the model stated, because the "
                   "incident held thin evidence")
_WAITING = Gauge("kaval_diagnosis_waiting",
                 "Open incidents that should be diagnosed and have no proposal yet")
_OLDEST = Gauge("kaval_diagnosis_oldest_wait_seconds",
                "How long the oldest of those has waited since it opened")
_LAST = Gauge("kaval_diagnosis_last_seconds", "How long the last diagnosis took")

# The only values `outcome` ever takes.
OK = "ok"                    # a proposal with an action was written
OK_NO_FIX = "ok_no_fix"      # a proposal with no action (no fix exists for this cause)
MODEL_ERROR = "model_error"  # Ollama down, too slow, or two replies that broke the rules
BACKING_OFF = "backing_off"  # waiting out the pause after a failure; the model is not asked
RATE_LIMITED = "rate_limited"
IDLE = "idle"                # nothing was waiting


@dataclass(frozen=True)
class Settings:
    max_age: timedelta
    max_per_hour: int
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    @classmethod
    def from_env(cls) -> Settings:
        """`DIAGNOSE_MAX_AGE_HOURS`, `DIAGNOSE_MAX_PER_HOUR` and `DIAGNOSE_TIMEOUT_SECONDS` (one
        model call). A value that is not a positive number falls back to the default rather than
        switching the limit off."""
        return cls(
            max_age=timedelta(hours=_positive(os.environ.get("DIAGNOSE_MAX_AGE_HOURS"),
                                              DEFAULT_MAX_AGE_HOURS)),
            max_per_hour=int(_positive(os.environ.get("DIAGNOSE_MAX_PER_HOUR"),
                                       DEFAULT_MAX_PER_HOUR)),
            timeout_seconds=_positive(os.environ.get("DIAGNOSE_TIMEOUT_SECONDS"),
                                      DEFAULT_TIMEOUT_SECONDS),
        )


def _positive(raw: str | None, default: float) -> float:
    try:
        value = float(raw) if raw is not None else default
    except ValueError:
        return default
    return value if value > 0 else default


@dataclass
class Backoff:
    """After a model failure, no attempt until `until`; each failure in a row doubles the wait."""

    failures: int = 0
    until: datetime | None = None

    def failed(self, now: datetime) -> None:
        self.failures += 1
        wait = min(BACKOFF_FIRST * (2 ** (self.failures - 1)), BACKOFF_MAX)
        self.until = now + wait

    def worked(self) -> None:
        self.failures, self.until = 0, None

    def waiting(self, now: datetime) -> bool:
        return self.until is not None and now < self.until


@dataclass
class PassResult:
    outcome: str
    incident_id: uuid.UUID | None = None
    waiting: int = 0
    seconds: float = 0.0
    detail: str = ""


def waiting_incidents(
    session: Session, now: datetime, max_age: timedelta, *, skip_removable: bool = False
) -> list[Incident]:
    """Open, recent incidents with no proposal, oldest first, minus the ones for a person.
    With removal switched on (`skip_removable`), also minus the two causes the removal planner
    files by rule (KAV-95): the model is never asked what to delete."""
    has_proposal = select(Proposal.id).where(Proposal.incident_id == Incident.id).exists()
    rows = session.scalars(
        select(Incident)
        .where(Incident.closed_at.is_(None), Incident.opened_at >= now - max_age,
               ~has_proposal)
        .order_by(Incident.opened_at, Incident.id)
        .limit(CANDIDATES)
    ).all()
    return [
        i
        for i in rows
        if not health.is_people_only_fingerprint(i.fingerprint)
        and not (
            skip_removable
            and health.cause_of_fingerprint(i.fingerprint) in health.REMOVABLE_CAUSES
        )
    ]


def proposals_in_last_hour(session: Session, now: datetime, model: str) -> int:
    return int(session.scalar(
        select(func.count()).select_from(Proposal)
        .where(Proposal.model == model, Proposal.created_at >= now - timedelta(hours=1))
    ) or 0)


def diagnose_one_pass(
    session: Session, *, backoff: Backoff, settings: Settings, model: str,
    now: datetime | None = None,
    diagnose_fn: Callable[[Context], tuple[Diagnosis, ModelUsage]] | None = None,
    context_fn: Callable[[Session, Incident], Context] = build_context,
) -> PassResult | None:
    """One pass. Returns None when another copy holds the lock. Never raises for a model
    failure: that is an outcome, so the loop can carry on and say so."""
    now = now or datetime.now(UTC)
    if not session.scalar(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": LOCK_KEY}):
        return None
    queue = waiting_incidents(
        session, now, settings.max_age, skip_removable=removal.Settings.from_env().enabled
    )
    _WAITING.set(len(queue))
    _OLDEST.set(max(((now - i.opened_at).total_seconds() for i in queue), default=0))
    if not queue:
        session.rollback()  # ends the transaction, which releases the lock
        return PassResult(IDLE)
    if backoff.waiting(now):
        session.rollback()
        return PassResult(BACKING_OFF, waiting=len(queue), detail="paused after a failure")
    if proposals_in_last_hour(session, now, model) >= settings.max_per_hour:
        session.rollback()
        return PassResult(RATE_LIMITED, waiting=len(queue))

    incident = queue[0]
    ask = diagnose_fn or (lambda ctx: diagnose(ctx, timeout=settings.timeout_seconds))
    started = time.monotonic()
    try:
        context = context_fn(session, incident)
        diagnosis, usage = ask(context)
    except (DiagnosisError, EmbeddingError, httpx.HTTPError) as exc:  # the model side, not a bug
        session.rollback()
        backoff.failed(now)
        return PassResult(MODEL_ERROR, incident.id, len(queue), time.monotonic() - started,
                          detail=f"{type(exc).__name__}: {exc}")
    proposal = write_proposal(session, incident, diagnosis, usage, model=model,
                              cost_usd=Decimal("0"))
    backoff.worked()
    outcome = OK if diagnosis.actions else OK_NO_FIX
    detail = f"proposal {proposal.id}, {len(diagnosis.actions)} action(s)"
    if proposal.confidence < diagnosis.confidence:
        _LIMITED.inc()
        detail += (f", confidence {proposal.confidence:.2f} "
                   f"(the model said {diagnosis.confidence:.2f}; thin evidence)")
    return PassResult(outcome, incident.id, len(queue) - 1, time.monotonic() - started,
                      detail=detail)


def record(result: PassResult | None) -> list[str]:
    """Count the outcome and return the lines to print. Incident ids are printed (logs are for
    people with access) but never put in a metric label."""
    if result is None:
        return ["another diagnoser holds the lock; skipped this pass"]
    if result.outcome not in (IDLE, BACKING_OFF):
        _DIAGNOSES.labels(result.outcome).inc()
    if result.seconds:
        _LAST.set(result.seconds)
    if result.outcome == IDLE:
        return ["nothing waiting"]
    who = f" {result.incident_id}" if result.incident_id else ""
    return [f"{result.outcome}{who}  ({result.waiting} still waiting)  {result.detail}".rstrip()]


def runbooks_dir() -> Path:
    return Path(os.environ.get("KAVAL_RUNBOOKS_DIR", "/app/runbooks"))


def sync_runbooks_once(session: Session) -> list[str]:
    """Bring the runbook index in line with the runbooks shipped in this image, so the model
    reads the same runbooks the people do (they are versioned with the image, ADR-0055).
    Idempotent: re-embeds only what changed."""
    from kaval_agent.index_runbooks import read_runbooks, sync_runbooks

    current = read_runbooks(runbooks_dir())
    result = sync_runbooks(session, current)
    return [f"runbooks: {len(result.created)} new, {len(result.updated)} changed, "
            f"{len(result.unchanged)} unchanged, {len(result.deleted)} removed"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m kaval_agent.diagnoser",
        description="Diagnose new incidents with the local model and write proposals.",
    )
    parser.add_argument("--every", type=float, metavar="SECONDS",
                        help="keep running, one pass every SECONDS; default: one pass")
    parser.add_argument("--version", action="version", version=f"kaval-diagnoser {__version__}")
    args = parser.parse_args(argv)

    from kaval_shared.db import get_engine  # only a real run needs the database

    model = os.environ.get("LOCAL_MODEL", "")
    if not model:
        sys.exit("LOCAL_MODEL is not set: there is no model to ask")
    settings = Settings.from_env()
    backoff = Backoff()
    if args.every:
        # A pass can legitimately last two model calls (two attempts), so "stalled" must not mean
        # "longer than a few intervals" for this loop: declare the longest healthy gap.
        metrics.serve(SERVICE, interval=args.every + 2 * settings.timeout_seconds,
                      version=__version__)
    runbooks_ready = False
    try:
        while True:
            worked = True
            # The removal planner needs no model, so it runs whether or not the model is up. Its own
            # try: a bug here must not stop diagnosis, nor the reverse.
            try:
                with Session(get_engine()) as session:
                    for line in removal.plan_pass(session, removal.Settings.from_env()):
                        print(line, flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"removal planner failed: {type(exc).__name__}", flush=True)
            if not runbooks_ready:
                try:
                    with Session(get_engine()) as session:
                        for line in sync_runbooks_once(session):
                            print(line, flush=True)
                    runbooks_ready = True
                except Exception as exc:  # noqa: BLE001 - the model may still be starting
                    worked = False
                    print(f"runbooks: not synced yet ({type(exc).__name__}: {exc})", flush=True)
            if runbooks_ready:
                with Session(get_engine()) as session:
                    result = diagnose_one_pass(session, backoff=backoff, settings=settings,
                                               model=model)
                for line in record(result):
                    print(line, flush=True)
                worked = result is None or result.outcome not in (MODEL_ERROR, BACKING_OFF)
            # The loop itself is alive and did its job (it tried and said so), so the success
            # time moves either way: "the loop is stuck" is KavalLoopStalled's question. That
            # the model is failing is a different one, answered by the error count and by
            # KavalDiagnosisBacklog (an incident has waited too long): the two do not page twice.
            if not worked:
                metrics.pass_failed(SERVICE)
            metrics.pass_ok(SERVICE)
            if not args.every:
                return 0 if worked else 1
            time.sleep(args.every)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
