"""The health numbers every Kaval service shows about itself (KAV-78, ADR-0042).

Prometheus reads them from `GET :9100/metrics`. Only three things are Kaval's own:

    kaval_loop_last_success_timestamp_seconds{service}   when the last full pass finished
    kaval_loop_interval_seconds{service}                 how often a pass is meant to happen
    kaval_loop_errors_total{service}                     passes that failed

plus the standard process numbers the client library adds (memory, CPU, open files), which is
what makes "how much of the 4 GB does each service really use" a measurement and not a guess.

A stalled loop is the failure a pod's own health check cannot see: the process is alive, the
port answers, and nothing has been done for an hour. So the alert is "the last success is older
than a few intervals", worked out in Prometheus from these two numbers (see
`deploy/charts/kaval/files/kaval-alerts.rules.yaml`), and a pod that has died altogether shows up
as `up == 0`.

**Nothing sensitive goes in here, ever.** The cluster's network (Flannel) cannot restrict which
pod may read another's port, so anything printed here is readable by every pod in the cluster.
There are no incident ids, fingerprints, targets or error messages: only service names, counts
and times. Adding a label with a value that comes from data is a bug.

**A failure here never stops the work.** If the port cannot be opened (a second copy running on a
laptop, say) the service says so once and carries on without it.
"""

from __future__ import annotations

import os
import sys
import time

from prometheus_client import Counter, Gauge, start_http_server

PORT = 9100

_LAST_SUCCESS = Gauge(
    "kaval_loop_last_success_timestamp_seconds",
    "Unix time the last full pass of this service's loop finished", ["service"],
)
_INTERVAL = Gauge(
    "kaval_loop_interval_seconds", "Seconds between passes this service is meant to make",
    ["service"],
)
_ERRORS = Counter("kaval_loop_errors", "Passes that failed", ["service"])
_BUILD = Gauge("kaval_build_info", "Which version of this service is running",
               ["service", "version"])


def serve(service: str, *, interval: float | None = None, version: str = "unknown") -> bool:
    """Start the metrics port for `service`; return whether it is up.

    `interval` is the loop's period in seconds, or None for a service that is driven by requests
    rather than a loop (the gateway), which gets no staleness rule. Setting `KAVAL_METRICS_PORT`
    to 0 turns the port off.
    """
    port = int(os.environ.get("KAVAL_METRICS_PORT", PORT))
    if interval:
        _INTERVAL.labels(service).set(interval)
        # A service that has not finished a pass yet is young, not stalled: start the clock now.
        _LAST_SUCCESS.labels(service).set(time.time())
    _BUILD.labels(service, version).set(1)
    if port == 0:
        return False
    try:
        start_http_server(port)
    except OSError as exc:  # the work matters more than the numbers about it
        print(f"metrics: not serving on :{port}: {exc}", file=sys.stderr, flush=True)
        return False
    return True


def pass_ok(service: str) -> None:
    """Call after every full pass of the loop that did what it is for."""
    _LAST_SUCCESS.labels(service).set(time.time())


def pass_failed(service: str) -> None:
    """Call when a pass failed but the loop is carrying on. Does not move the success time."""
    _ERRORS.labels(service).inc()
