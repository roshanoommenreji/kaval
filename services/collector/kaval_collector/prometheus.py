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

from kaval_shared.models import Signal
from sqlalchemy import select
from sqlalchemy.orm import Session

from kaval_collector import __version__
from kaval_collector.synthetic import DEMO_NAMESPACE, PROMETHEUS

KIND = "container_memory_near_limit"
DEFAULT_URL = "http://localhost:9090"
THRESHOLD = 0.90  # fraction of the memory limit
RENEW_SECONDS = 300.0


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
    parser.add_argument("--threshold", type=float, default=THRESHOLD,
                        help="fraction of the memory limit that counts as near it (default 0.9)")
    parser.add_argument("--renew", type=float, default=RENEW_SECONDS, metavar="SECONDS",
                        help="while a container stays near its limit, write a fresh signal this "
                             "often so its incident stays open (default 300)")
    parser.add_argument("--every", type=float, metavar="SECONDS",
                        help="keep running, one pass every SECONDS; default: one pass")
    parser.add_argument("--version", action="version", version=f"kaval-collector {__version__}")
    args = parser.parse_args(argv)

    from kaval_shared.db import get_engine  # only a real run needs the database

    prom = HttpPrometheus(args.url)
    try:
        while True:
            try:
                with Session(get_engine()) as session:
                    lines = poll(session, prom, args.namespace,
                                 threshold=args.threshold, renew_seconds=args.renew)
                for line in report(lines):
                    print(line, flush=True)
            except PrometheusError as exc:
                # A Prometheus that is down must be visible, and must not stop the loop: the
                # next pass may find it back. A single pass has nothing to wait for, so it fails.
                print(f"prometheus unreachable: {exc}", file=sys.stderr, flush=True)
                if not args.every:
                    return 1
            if not args.every:
                return 0
            time.sleep(args.every)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
