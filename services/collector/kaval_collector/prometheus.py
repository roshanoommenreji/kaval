"""Asks Prometheus which containers are near their memory limit and writes `signal` rows (KAV-75).

The second real source after `kaval_collector.k8s_events` (ADR-0022 deferred it; ADR-0040 is this
design). Kubernetes events say a container *has* been killed; this says one is about to be. Same
`signal` table, and the same value shape `kaval_collector.synthetic` and the golden evals already
use for `container_memory_near_limit`, so `kaval_agent.correlate.cause_of` needs no change.

An event is a thing that happened once; a metric is a level that stays true. That changes the
de-duplication. `k8s_events` writes a new signal only when the event's `count` grows. A container
sitting at 95% of its limit has no count, so this writes one signal per container, then another
every `--renew` seconds while it stays above the line. Writing only the first would repeat the
trap ADR-0022 names for events: `kaval_agent.correlate` closes an incident after 15 quiet
minutes, so one signal would close the incident while the container is still at the limit. Writing
one per poll would flood the table.

Two plain PromQL queries and the ratio worked out here, rather than one clever division in
PromQL: a limit of 0 means "no limit", which PromQL would turn into an infinite ratio, and a
threshold test in Python is a unit test rather than a live experiment.

Second job (KAV-78, ADR-0042): the same poller also reads Prometheus's own list of firing alerts
(`ALERTS`), because that is how Kaval learns that one of its own services has stopped. Polling the
list is the whole reason Alertmanager is not adopted (ADR-0043). It also watches Prometheus
itself: a Prometheus that cannot be asked for `UNREACHABLE_AFTER` passes in a row, or that answers
but is not evaluating its rules (the always-firing `KavalWatchdog` is missing), becomes a
`kaval_*` signal, the one failure no rule inside Prometheus could report.

Third and fourth jobs (KAV-81, ADR-0047): the same poller also notices a nearly full disk (a
ratio Prometheus is asked for, the query being a setting so the measurement on staging, not a guess,
decides what it is) and, through `kaval_collector.probe`, a service that has become slow. Both
become incidents that are for a person, never a proposed fix.

Talks to Prometheus with the standard library, so the collector image gains no dependency.
It needs no Kubernetes API access at all, which is why the chart runs it as its own Deployment
with no token mounted (deploy/charts/kaval/templates/prometheus.yaml).

    python -m kaval_collector.prometheus              # one pass
    python -m kaval_collector.prometheus --every 30   # keep going, the deployed shape
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from kaval_shared import metrics
from kaval_shared.models import Signal
from sqlalchemy import select
from sqlalchemy.orm import Session

from kaval_collector import __version__, probe
from kaval_collector.synthetic import DEMO_NAMESPACE, PROMETHEUS

KIND = "container_memory_near_limit"
DEFAULT_URL = "http://localhost:9090"
THRESHOLD = 0.90  # fraction of the memory limit
RENEW_SECONDS = 300.0
ALERT_KIND = "alert_firing"
WATCHDOG = "KavalWatchdog"
# Passes in a row without an answer before "Prometheus is down" is a signal. One miss is a restart.
UNREACHABLE_AFTER = 3
# How long Prometheus may answer without the watchdog before "rules not running" is a signal. A
# restarted Prometheus answers at once but takes a while to evaluate its rules the first time:
# measured on staging at 65 s (12:22:36 to 12:23:41), and 83 s in the first quiet check. 150 s is
# a little over twice the worst seen. Counted in time, not passes: 3 passes of 30 s sit inside
# the gap, which is how the first attempt at this fix still raised a false alarm.
WATCHDOG_GRACE = timedelta(seconds=150)
DISK_KIND = "disk_near_full"
DISK_THRESHOLD = 0.85  # fraction of the disk used. Below this the poller says nothing.
# What the disk signal's target is named for: the app server's own disk, or the database server's
# (KAV-82, ADR-0050). Same kind, same incident cause, same lane; only the subject differs.
NODE_DISK = "node-disk"
DATABASE_DISK = "database-disk"
UNREACHABLE = "kaval_prometheus_unreachable"
WATCHDOG_MISSING = "kaval_watchdog_missing"


@dataclass
class Health:
    """What the poller has seen across passes, so one odd pass is not mistaken for an outage.
    A Prometheus that has just started answers before it has evaluated its rules once, so the
    watchdog is briefly absent (found live on staging, KAV-78): that must not page anyone."""

    watchdog_missing_since: datetime | None = None


class PrometheusError(RuntimeError):
    """Prometheus could not be asked, or answered with something that is not a result."""


@dataclass(frozen=True)
class Sample:
    labels: dict[str, str]
    value: float


class Prometheus(Protocol):
    """The one call `poll` makes. `HttpPrometheus` satisfies it, and so does the test fake."""

    def query(self, promql: str) -> list[Sample]: ...


class HttpPrometheus:
    """An instant query, `GET /api/v1/query`, answered as a list of samples."""

    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        if urllib.parse.urlparse(base_url).scheme not in ("http", "https"):
            raise ValueError(f"not an http(s) URL: {base_url!r}")
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    def query(self, promql: str) -> list[Sample]:
        url = f"{self._base}/api/v1/query?{urllib.parse.urlencode({'query': promql})}"
        try:
            with urllib.request.urlopen(url, timeout=self._timeout) as response:
                body = json.load(response)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise PrometheusError(f"{self._base}: {exc}") from exc
        return parse_result(body)


def parse_result(body: object) -> list[Sample]:
    """Prometheus's instant-query answer -> samples. Anything but a vector is an error, loudly."""
    if not isinstance(body, dict) or body.get("status") != "success":
        raise PrometheusError(f"query failed: {body!r}"[:200])
    data = body.get("data")
    if not isinstance(data, dict) or data.get("resultType") != "vector":
        raise PrometheusError("query did not return a vector")
    samples: list[Sample] = []
    for item in data.get("result", []):
        metric, pair = item.get("metric", {}), item.get("value", [])
        if len(pair) != 2:
            continue
        samples.append(Sample(
            labels={str(k): str(v) for k, v in metric.items()}, value=float(pair[1]),
        ))
    return samples


def _selector(metric: str, namespace: str) -> str:
    # container="" is the pod's own cgroup and "POD" the pause container: neither is a workload.
    return f'{metric}{{namespace="{namespace}",container!="",container!="POD"}}'


def near_limit(
    usage: Sequence[Sample], limits: Sequence[Sample], threshold: float,
) -> list[tuple[str, str, int, int]]:
    """(pod, container, used_bytes, limit_bytes) for every container above `threshold`.
    No limit (0, or no series at all) is not a problem this can name, so it is skipped."""
    limit_of = {(s.labels.get("pod"), s.labels.get("container")): s.value for s in limits}
    found: list[tuple[str, str, int, int]] = []
    for s in usage:
        pod, container = s.labels.get("pod"), s.labels.get("container")
        limit = limit_of.get((pod, container), 0.0)
        if pod and container and limit > 0 and s.value / limit >= threshold:
            found.append((pod, container, int(s.value), int(limit)))
    return sorted(found)


def _recently_written(
    session: Session, target: str, container: str, since: datetime,
) -> bool:
    return session.scalar(
        select(Signal.id)
        .where(
            Signal.source == PROMETHEUS, Signal.kind == KIND, Signal.target == target,
            Signal.value.contains({"container": container}), Signal.observed_at >= since,
        )
        .limit(1)
    ) is not None


def poll(
    session: Session, prom: Prometheus, namespace: str = DEMO_NAMESPACE, *,
    threshold: float = THRESHOLD, renew_seconds: float = RENEW_SECONDS,
    now: datetime | None = None,
) -> list[str]:
    """One pass: every container in `namespace` above `threshold` of its memory limit gets a
    signal unless one was written for it in the last `renew_seconds`. One commit, at the end."""
    now = now or datetime.now(UTC)
    usage = prom.query(_selector("container_memory_working_set_bytes", namespace))
    limits = prom.query(_selector("container_spec_memory_limit_bytes", namespace) + " > 0")
    report: list[str] = []
    for pod, container, used, limit in near_limit(usage, limits, threshold):
        target = f"{namespace}/{pod}"
        if _recently_written(session, target, container, now - timedelta(seconds=renew_seconds)):
            continue
        session.add(Signal(
            source=PROMETHEUS, kind=KIND, target=target, observed_at=now,
            value={
                "metric": "container_memory_working_set_bytes", "container": container,
                "value_bytes": used, "limit_bytes": limit,
            },
        ))
        report.append(f"{KIND:28} {target}  {container} {used / limit:.0%} of limit")
    session.commit()
    return report


def disk_over(samples: Sequence[Sample], threshold: float) -> Sample | None:
    """The fullest series at or above `threshold`, or None. A "used fraction" is between 0 and 1:
    anything else (NaN from 0/0, +Inf from a division by a missing size, a negative or a ratio
    above 1) is a broken query, not a full disk, and is ignored rather than paged on."""
    valid = [s for s in samples if 0.0 < s.value <= 1.0]
    over = [s for s in valid if s.value >= threshold]
    return max(over, key=lambda s: s.value) if over else None


def poll_disk(
    session: Session, prom: Prometheus, kaval_namespace: str, query: str, *,
    threshold: float = DISK_THRESHOLD, renew_seconds: float = RENEW_SECONDS,
    now: datetime | None = None, subject: str = NODE_DISK,
) -> list[str]:
    """One pass: if the disk is at or over `threshold`, write a signal unless one was written in
    the last `renew_seconds` (the same renew-while-it-persists reasoning as the memory signals).
    `query` must return the used fraction (0 to 1) of whatever disk is being watched; an empty
    query means the check is off. One signal for the node, naming the fullest mount."""
    if not query:
        return []
    now = now or datetime.now(UTC)
    found = disk_over(prom.query(query), threshold)
    if found is None:
        return []
    target = f"{kaval_namespace}/{subject}"
    if _written_since(session, target, DISK_KIND, now - timedelta(seconds=renew_seconds)):
        return []
    session.add(Signal(
        source=PROMETHEUS, kind=DISK_KIND, target=target, observed_at=now,
        # Our own measurement only: a fraction, the threshold, and a short mount name.
        value={"used_fraction": round(found.value, 3), "threshold": threshold,
               "mount": found.labels.get("mountpoint", found.labels.get("device", ""))[:64]},
    ))
    session.commit()
    return [f"{DISK_KIND:28} {target}  {found.value:.0%} used"]


def _written_since(session: Session, target: str, kind: str, since: datetime,
                   alertname: str | None = None) -> bool:
    query = select(Signal.id).where(
        Signal.source == PROMETHEUS, Signal.kind == kind, Signal.target == target,
        Signal.observed_at >= since,
    )
    if alertname is not None:
        query = query.where(Signal.value.contains({"alertname": alertname}))
    return session.scalar(query.limit(1)) is not None


def poll_alerts(
    session: Session, prom: Prometheus, kaval_namespace: str, *,
    renew_seconds: float = RENEW_SECONDS, now: datetime | None = None,
    health: Health | None = None,
) -> list[str]:
    """Every firing Kaval-health alert becomes a signal, renewed while it keeps firing (the same
    reasoning as the memory signals above). Also checks the always-firing watchdog: if Prometheus
    answers but that alert is absent for `WATCHDOG_GRACE` (`health` carries when it was first missed
    between passes), its rules are not being evaluated, and no rule can say so. Without a `health`,
    a single miss counts: only for callers that debounce themselves."""
    now = now or datetime.now(UTC)
    since = now - timedelta(seconds=renew_seconds)
    lines: list[str] = []
    for sample in prom.query('ALERTS{alertstate="firing",kaval_health="true"}'):
        name, service = sample.labels.get("alertname"), sample.labels.get("service")
        if not name or not service:
            continue
        target = f"{kaval_namespace}/{service}"
        if _written_since(session, target, ALERT_KIND, since, alertname=name):
            continue
        session.add(Signal(
            source=PROMETHEUS, kind=ALERT_KIND, target=target, observed_at=now,
            value={"alertname": name, "severity": sample.labels.get("severity", "critical"),
                   "service": service},
        ))
        lines.append(f"{ALERT_KIND:28} {target}  {name}")
    if prom.query(f'ALERTS{{alertname="{WATCHDOG}",alertstate="firing"}}'):
        if health is not None:
            health.watchdog_missing_since = None
    else:
        if health is not None and health.watchdog_missing_since is None:
            health.watchdog_missing_since = now
        if health is None or now - (health.watchdog_missing_since or now) >= WATCHDOG_GRACE:
            lines += _health_signal(session, kaval_namespace, WATCHDOG_MISSING, since, now)
    session.commit()
    return lines


def _health_signal(session: Session, kaval_namespace: str, kind: str, since: datetime,
                   now: datetime) -> list[str]:
    target = f"{kaval_namespace}/prometheus"
    if _written_since(session, target, kind, since):
        return []
    session.add(Signal(source=PROMETHEUS, kind=kind, target=target, observed_at=now,
                       value={"service": "prometheus"}))
    return [f"{kind:28} {target}"]


def record_unreachable(
    session: Session, kaval_namespace: str, *, renew_seconds: float = RENEW_SECONDS,
    now: datetime | None = None,
) -> list[str]:
    """Prometheus has not answered for `UNREACHABLE_AFTER` passes. Written to the database, which
    is up even when Prometheus is not; renewed while it stays down."""
    now = now or datetime.now(UTC)
    lines = _health_signal(session, kaval_namespace, UNREACHABLE,
                           now - timedelta(seconds=renew_seconds), now)
    session.commit()
    return lines


def report(lines: Sequence[str]) -> list[str]:
    return list(lines) or ["nothing new"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m kaval_collector.prometheus",
        description="Ask Prometheus which containers are near their memory limit and write "
                    "them as signal rows.",
    )
    parser.add_argument("--url", default=os.environ.get("PROMETHEUS_URL", DEFAULT_URL))
    parser.add_argument("--namespace", default=DEMO_NAMESPACE)
    parser.add_argument("--kaval-namespace", default=os.environ.get("KAVAL_NAMESPACE", "kaval"),
                        help="where Kaval's own services run, for health alerts (default: "
                             "$KAVAL_NAMESPACE, which the chart sets)")
    parser.add_argument("--threshold", type=float, default=THRESHOLD,
                        help="fraction of the memory limit that counts as near it (default 0.9)")
    parser.add_argument("--renew", type=float, default=RENEW_SECONDS, metavar="SECONDS",
                        help="while a container stays near its limit, write a fresh signal this "
                             "often so its incident stays open (default 300)")
    parser.add_argument("--disk-query", default=os.environ.get("DISK_QUERY", ""),
                        help="PromQL returning the used fraction (0 to 1) of the disk to watch; "
                             "empty (the default) turns the disk check off")
    parser.add_argument("--disk-threshold", type=float, default=DISK_THRESHOLD,
                        help="fraction of the disk that counts as nearly full (default 0.85)")
    parser.add_argument("--database-disk-query",
                        default=os.environ.get("DATABASE_DISK_QUERY", ""),
                        help="the same, for the database server's disks (KAV-82); empty (the "
                             "default) turns that check off")
    parser.add_argument("--database-disk-threshold", type=float, default=DISK_THRESHOLD,
                        help="fraction of the database server's disk that counts as nearly full")
    parser.add_argument("--probe-targets", default=os.environ.get("PROBE_TARGETS", ""),
                        help="services to time with one small request each pass, as "
                             "namespace/name=url,... Settings only; empty turns it off")
    parser.add_argument("--probe-slow-seconds", type=float, default=probe.SLOW_SECONDS,
                        help="an answer slower than this counts as slow (default 1)")
    parser.add_argument("--every", type=float, metavar="SECONDS",
                        help="keep running, one pass every SECONDS; default: one pass")
    parser.add_argument("--version", action="version", version=f"kaval-collector {__version__}")
    args = parser.parse_args(argv)

    from kaval_shared.db import get_engine  # only a real run needs the database

    prom = HttpPrometheus(args.url)
    try:
        probe_targets = probe.parse_targets(args.probe_targets)
    except ValueError as exc:
        sys.exit(f"--probe-targets: {exc}")
    slowness = probe.Slowness()
    if args.every:
        metrics.serve("collector-prometheus", interval=args.every, version=__version__)
    misses = 0
    health = Health()
    try:
        while True:
            # The slowness probe asks the services directly, not Prometheus, so it runs before and
            # apart from the Prometheus questions: a Prometheus outage must not blind it.
            if probe_targets:
                try:
                    with Session(get_engine()) as session:
                        for line in probe.poll(session, probe_targets, slowness,
                                               slow_seconds=args.probe_slow_seconds,
                                               renew_seconds=args.renew):
                            print(line, flush=True)
                except Exception as exc:  # noqa: BLE001 - one bad pass must not stop the loop
                    print(f"probe pass failed: {type(exc).__name__}: {exc}",
                          file=sys.stderr, flush=True)
            try:
                with Session(get_engine()) as session:
                    lines = poll(session, prom, args.namespace,
                                 threshold=args.threshold, renew_seconds=args.renew)
                    lines += poll_alerts(session, prom, args.kaval_namespace,
                                         renew_seconds=args.renew, health=health)
                    lines += poll_disk(session, prom, args.kaval_namespace, args.disk_query,
                                       threshold=args.disk_threshold, renew_seconds=args.renew)
                    lines += poll_disk(session, prom, args.kaval_namespace,
                                       args.database_disk_query,
                                       threshold=args.database_disk_threshold,
                                       renew_seconds=args.renew, subject=DATABASE_DISK)
                for line in report(lines):
                    print(line, flush=True)
                misses = 0
                metrics.pass_ok("collector-prometheus")
            except PrometheusError as exc:
                # A Prometheus that is down must be visible, and must not stop the loop: the
                # next pass may find it back. A single pass has nothing to wait for, so it fails.
                print(f"prometheus unreachable: {exc}", file=sys.stderr, flush=True)
                metrics.pass_failed("collector-prometheus")
                if not args.every:
                    return 1
                misses += 1
                if misses >= UNREACHABLE_AFTER:
                    with Session(get_engine()) as session:
                        for line in record_unreachable(session, args.kaval_namespace,
                                                       renew_seconds=args.renew):
                            print(line, flush=True)
            if not args.every:
                return 0
            time.sleep(args.every)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
