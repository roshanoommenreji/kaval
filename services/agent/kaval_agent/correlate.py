"""Signal correlation: raw signals become incidents with stable fingerprints (KAV-39, ADR-0014).

One failure produces many signals: a memory warning, an OOM kill, three restarts, an alert.
That is one incident, not six. And when the same failure comes back next week it should get
the same fingerprint, so the context builder can find what happened last time and whether
the fix worked. Three rules decide it, all plain code, none of it the model's job:

- Subject: which thing is broken. For Kubernetes that's the workload
  (`kaval-demo/checkout`), not the pod (`checkout-7f9c4-x2klp`): a restarted pod gets a new
  name and is still the same problem. For AWS it's the resource (`ebs/vol-0…`).
- Cause: what kind of broken, read from the signal's kind and payload. The most specific
  cause in a group names the incident: `oom_killed` beats `crashloop`, because the restarts
  are the symptom and the kill is the reason.
- Time: a new group waits GROUP_WAIT before it opens, so its cause has arrived by then. An
  incident closes when its domain's quiet window passes with no new problem signal. If the
  failure comes back after that, it's a new incident with the same fingerprint, which is how
  history builds up.

The fingerprint is `cause:domain:subject`, e.g. `oom_killed:k8s:kaval-demo/checkout`,
readable on the phone without a lookup table.

    python -m kaval_agent.correlate              # one pass (make correlate)
    python -m kaval_agent.correlate --every 30   # keep going, one pass every 30 s
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from kaval_shared.models import Incident, IncidentSignal, Severity, Signal
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from kaval_agent import __version__

# Signal source -> domain. The domain sets the quiet window and is part of the fingerprint.
DOMAINS = {
    "kubernetes": "k8s",
    "prometheus": "k8s",
    "alertmanager": "k8s",
    "cost_explorer": "aws",
    "aws_ec2": "aws",
}
# How long with no new problem signal before an incident counts as over. Kubernetes signals
# arrive within seconds; AWS cost and inventory data is daily and ~24 h late, so a shorter
# window would close a cost incident between two of its own data points.
QUIET = {"k8s": timedelta(minutes=15), "aws": timedelta(hours=48)}
# A new group waits this long before it opens, as Alertmanager's group_wait does: the OOM
# kill can land a few seconds after the first restart, and the fingerprint is fixed at
# opening (rows are append-only), so opening on the first signal would name it wrongly.
GROUP_WAIT = timedelta(seconds=60)
# How far back to look for unlinked signals. A week of daily cost is the spike baseline.
LOOKBACK = timedelta(days=8)
BASELINE = timedelta(days=7)
SPIKE_FACTOR = Decimal(3)
MIN_BASELINE_DAYS = 3
# Postgres advisory lock id: two correlators at once would open the same incident twice.
LOCK_KEY = 0x4B41564C_0001  # "KAVL", lock 1

_SEVERITY_ORDER = [Severity.low, Severity.medium, Severity.high, Severity.critical]
_ALERT_SEVERITY = {"critical": Severity.high, "warning": Severity.medium}

# Kubernetes pod names: <workload>-<replicaset hash>-<pod hash> for a Deployment,
# <workload>-<pod hash> for a DaemonSet, <workload>-<ordinal> for a StatefulSet. The hashes
# use this alphabet (no vowels, no 0/1/3, so they never spell words).
_HASH = "[bcdfghjklmnpqrstvwxz2456789]"
_GENERATED = re.compile(rf"^(?P<workload>.+?)(?:-{_HASH}{{5,10}})?-{_HASH}{{5}}$")
_ORDINAL = re.compile(r"^(?P<workload>.+)-\d+$")


def workload(pod: str) -> str:
    """`checkout-7f9c4d8b6-x2klp` -> `checkout`. A name with no generated suffix is kept."""
    for pattern in (_GENERATED, _ORDINAL):
        if m := pattern.match(pod):
            return m.group("workload")
    return pod


def subject(signal: Signal) -> str:
    """Which thing is broken: namespace/workload for Kubernetes, the resource for AWS."""
    if DOMAINS.get(signal.source) == "k8s" and "/" in signal.target:
        namespace, pod = signal.target.split("/", 1)
        return f"{namespace}/{workload(pod)}"
    return signal.target


def _snake(name: str) -> str:
    """`KubeNodeNotReady` -> `kube_node_not_ready`: a cause name safe in a fingerprint."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    return re.sub(r"[^a-z0-9]+", "_", spaced.lower()).strip("_") or "unknown"


@dataclass(frozen=True)
class Cause:
    name: str
    rank: int  # lower is more specific; the most specific cause names the incident
    severity: Severity


def cause_of(signal: Signal, spikes: set[uuid.UUID]) -> Cause | None:
    """What a signal says is wrong, or None when it isn't a problem (a normal cost day)."""
    v, kind = signal.value, signal.kind
    if v.get("status") == "resolved":
        return None
    if kind == "pod_oom_killed" or v.get("reason") == "OOMKilled":
        return Cause("oom_killed", 1, Severity.high)
    if kind == "container_exited":
        if "exec format error" in str(v.get("message", "")):
            return Cause("exec_format", 1, Severity.high)
        return Cause("container_error", 2, Severity.medium)
    if kind == "pod_back_off":
        return Cause("crashloop", 3, Severity.medium)
    if kind == "alert_firing":
        alert = str(v.get("alertname", "alert"))
        name = "crashloop" if alert == "KubePodCrashLooping" else _snake(alert)
        return Cause(name, 3, _ALERT_SEVERITY.get(str(v.get("severity")), Severity.low))
    if kind == "container_memory_near_limit":
        return Cause("memory_pressure", 4, Severity.low)
    if kind == "daily_cost":
        return Cause("cost_spike", 1, Severity.medium) if signal.id in spikes else None
    if kind == "volume_unattached":
        return Cause("idle_volume", 1, Severity.low)
    # A kind nobody has classified yet still becomes an incident: a signal dropped silently
    # is worse than one filed as low severity under its own name.
    return Cause(_snake(kind), 5, Severity.low)


def _amount(signal: Signal) -> Decimal | None:
    try:
        return Decimal(str(signal.value["amount_usd"]))
    except (KeyError, InvalidOperation):
        return None


def cost_spikes(rows: Sequence[Signal]) -> set[uuid.UUID]:
    """Daily cost rows above SPIKE_FACTOR x the median of the week before, same target and
    service. The median, not the mean, so one earlier spike doesn't hide the next."""
    series: dict[tuple[str, str], list[tuple[datetime, Decimal, uuid.UUID]]] = {}
    seen: set[uuid.UUID] = set()
    for s in rows:
        if s.kind != "daily_cost" or s.id in seen or (amount := _amount(s)) is None:
            continue
        seen.add(s.id)
        series.setdefault((s.target, str(s.value.get("service"))), []).append(
            (s.observed_at, amount, s.id))
    spikes: set[uuid.UUID] = set()
    for points in series.values():
        for at, amount, sid in points:
            prior = [a for t, a, _ in points if at - BASELINE <= t < at]
            if len(prior) >= MIN_BASELINE_DAYS and amount > SPIKE_FACTOR * statistics.median(prior):
                spikes.add(sid)
    return spikes


@dataclass(frozen=True)
class OpenIncident:
    id: uuid.UUID
    fingerprint: str
    last_seen: datetime


@dataclass
class NewIncident:
    fingerprint: str
    severity: Severity
    opened_at: datetime
    signals: list[Signal]
    closed_at: datetime | None = None


@dataclass
class Plan:
    opened: list[NewIncident] = field(default_factory=list)
    attached: dict[uuid.UUID, list[Signal]] = field(default_factory=dict)
    closed: dict[uuid.UUID, datetime] = field(default_factory=dict)
    waiting: int = 0  # problem signals held back by GROUP_WAIT, picked up by a later pass

    @property
    def empty(self) -> bool:
        return not (self.opened or self.attached or self.closed)


@dataclass
class _Track:
    """One subject's run of problem signals: an incident already open, or a new group."""

    domain: str
    subject: str
    last_seen: datetime
    existing: uuid.UUID | None = None
    signals: list[Signal] = field(default_factory=list)
    causes: list[Cause] = field(default_factory=list)

    def add(self, signal: Signal, cause: Cause) -> None:
        self.signals.append(signal)
        self.causes.append(cause)
        self.last_seen = max(self.last_seen, signal.observed_at)


def _key(fingerprint: str) -> tuple[str, str]:
    _, domain, subj = fingerprint.split(":", 2)
    return domain, subj


def plan(new: Sequence[Signal], open_incidents: Sequence[OpenIncident], now: datetime,
         cost_history: Sequence[Signal] = ()) -> Plan:
    """Decide, without touching the database, what one pass should write.

    `new` is every signal not yet in an incident; `cost_history` adds older daily cost rows
    so a spike has a baseline. Pure, so every rule above is unit-tested without Postgres.
    """
    spikes = cost_spikes([*cost_history, *new])
    tracks: dict[tuple[str, str], _Track] = {}
    for inc in open_incidents:
        domain, subj = _key(inc.fingerprint)
        tracks[(domain, subj)] = _Track(domain, subj, inc.last_seen, existing=inc.id)
    finished: list[_Track] = []

    for signal in sorted(new, key=lambda s: s.observed_at):
        domain = DOMAINS.get(signal.source, signal.source)
        cause = cause_of(signal, spikes)
        if cause is None:
            continue
        key = (domain, subject(signal))
        track = tracks.get(key)
        quiet = QUIET.get(domain, QUIET["k8s"])
        if track is not None and signal.observed_at - track.last_seen > quiet:
            finished.append(track)  # the old run went quiet before this signal: it's over
            track = None
        if track is None:
            track = tracks[key] = _Track(domain, key[1], signal.observed_at)
        track.add(signal, cause)

    out = Plan()
    for track in [*finished, *tracks.values()]:
        quiet = QUIET.get(track.domain, QUIET["k8s"])
        ended = track.last_seen + quiet if now - track.last_seen >= quiet else None
        if track.existing is not None:
            if track.signals:
                out.attached[track.existing] = track.signals
            if ended is not None:
                out.closed[track.existing] = ended
            continue
        first = min(s.observed_at for s in track.signals)
        if now - first < GROUP_WAIT:
            out.waiting += len(track.signals)
            continue
        best = min(track.causes, key=lambda c: c.rank)  # ties: the earliest seen
        out.opened.append(NewIncident(
            fingerprint=f"{best.name}:{track.domain}:{track.subject}",
            severity=max((c.severity for c in track.causes), key=_SEVERITY_ORDER.index),
            opened_at=first,
            signals=track.signals,
            closed_at=ended,
        ))
    return out


def correlate(session: Session, now: datetime | None = None) -> Plan | None:
    """One pass, in one transaction. Returns None if another correlator holds the lock."""
    now = now or datetime.now(UTC)
    # Transaction-scoped: released at commit or rollback, so a crash can't leave it held.
    if not session.scalar(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": LOCK_KEY}):
        return None
    since = now - LOOKBACK
    linked = select(IncidentSignal.signal_id).where(IncidentSignal.signal_id == Signal.id)
    new = session.scalars(
        select(Signal).where(Signal.observed_at >= since, ~linked.exists())
        .order_by(Signal.observed_at, Signal.id)
    ).all()
    cost_targets = {s.target for s in new if s.kind == "daily_cost"}
    history = session.scalars(
        select(Signal).where(Signal.kind == "daily_cost", Signal.target.in_(cost_targets),
                             Signal.observed_at >= since - BASELINE)
    ).all() if cost_targets else []
    open_rows = session.execute(
        select(Incident.id, Incident.fingerprint, func.max(Signal.observed_at))
        .join(IncidentSignal, IncidentSignal.incident_id == Incident.id)
        .join(Signal, Signal.id == IncidentSignal.signal_id)
        .where(Incident.closed_at.is_(None))
        .group_by(Incident.id, Incident.fingerprint)
    ).all()
    result = plan(new, [OpenIncident(i, f, seen) for i, f, seen in open_rows], now, history)

    for n in result.opened:
        incident = Incident(fingerprint=n.fingerprint, severity=n.severity,
                            opened_at=n.opened_at, closed_at=n.closed_at)
        session.add(incident)
        session.flush()  # for its id
        session.add_all(IncidentSignal(incident_id=incident.id, signal_id=s.id)
                        for s in n.signals)
    for incident_id, signals in result.attached.items():
        session.add_all(IncidentSignal(incident_id=incident_id, signal_id=s.id)
                        for s in signals)
    for incident_id, closed_at in result.closed.items():
        # The one UPDATE the spine allows: an incident going from open to closed.
        session.execute(update(Incident)
                        .where(Incident.id == incident_id, Incident.closed_at.is_(None))
                        .values(closed_at=closed_at))
    session.commit()
    return result


def report(result: Plan | None) -> list[str]:
    if result is None:
        return ["another correlator holds the lock; skipped this pass"]
    lines = [
        f"opened    {n.fingerprint}  {n.severity.value}  {len(n.signals)} signals"
        + ("  (already over: closed)" if n.closed_at else "")
        for n in result.opened
    ]
    if result.attached:
        count = sum(len(s) for s in result.attached.values())
        lines.append(f"attached  {count} signals to {len(result.attached)} open incident(s)")
    if result.closed:
        lines.append(f"closed    {len(result.closed)} incident(s) gone quiet")
    if result.waiting:
        lines.append(f"waiting   {result.waiting} signals: a group opens "
                     f"{GROUP_WAIT.seconds} s after its first signal")
    return lines or ["nothing new"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m kaval_agent.correlate",
        description="Group unlinked signals into incidents.",
    )
    parser.add_argument("--every", type=float, metavar="SECONDS",
                        help="keep running, one pass every SECONDS; default: one pass")
    parser.add_argument("--version", action="version", version=f"kaval-agent {__version__}")
    args = parser.parse_args(argv)

    from kaval_shared.db import get_engine  # only a real run needs the database

    try:
        while True:
            with Session(get_engine()) as session:
                for line in report(correlate(session)):
                    print(line, flush=True)
            if not args.every:
                return 0
            time.sleep(args.every)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
