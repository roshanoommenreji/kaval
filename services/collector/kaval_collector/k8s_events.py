"""Watches real Kubernetes Events and writes them as `signal` rows (KAV-48).

Events, not a Pod watch, per `docs/learn/phase-3-kubernetes-local.md`'s own design: "the
collector normalises events into the signal table immediately. The database is the record;
the event stream is only the feed." Three properties that page names, designed around here:

- **They expire** (~1 h default retention). This module polls — `--every 15` by default —
  far more often than that, the same `--every` loop shape every other long-running
  component in this codebase already uses (`kaval_agent.correlate`,
  `kaval_executor.executor`), not a persistent `watch` stream. Simpler, and more robust to a
  dropped connection: a missed poll just means the next one sees it still, as long as it
  hasn't aged out.
- **They are deduplicated with a count**, not re-emitted as a new object per occurrence.
  Writing one `signal` per *poll* that sees an unchanged event would flood the table with
  identical rows; writing exactly one signal ever, keyed off the event object, would do the
  opposite — the exact "counting event objects instead of reading count" mistake that page
  lists as a common one. This module does neither: it remembers the highest `count` it has
  already written a signal for (per event, by the event object's own `uid`, by querying the
  last signal carrying that `event_uid`) and writes a new signal only when the real count has
  grown — so a crash-looping pod keeps producing fresh signals as it keeps crash-looping, and
  a one-off event produces exactly one.
- **They are not guaranteed.** A dropped event here is a missed signal, not an error this
  module can detect or retry — there is nothing to retry against. Documented, not solved.

Scoped to one namespace, `Warning`-type events only, and only ones whose `involvedObject` is
a `Pod` — the shape `kaval_agent.correlate.subject()` already expects
(`namespace/workload`, derived from a pod name). A `Node`- or `Deployment`-involved event
would need a different `target` shape this module doesn't produce yet; left for whenever
that's actually needed, not provisioned speculatively now (the same "RBAC and code grow
together" call `kaval_executor` made, ADR-0021).

**Why a `BackOff` event is not enough (KAV-80).** Kubernetes never emits an `OOMKilled` *event*:
that word lives in the pod's status (`lastState.terminated.reason`). What the event feed shows for
a program killed for memory is the same `BackOff` it shows for a bad release that crashes at
start-up, so on staging a real out-of-memory kill was labelled `crashloop` (the first game day).
The right fix differs (raise the limit, or roll back the release), so for a `BackOff` this module
reads the pod once and records why its container last ended; `OOMKilled` there makes the signal
`pod_oom_killed`. This needs `get` on pods in the demo namespace and nothing more.

Reason -> `kind` is a small, explicit table for the reasons `kaval_agent.correlate.cause_of`
specifically recognises (`OOMKilled`, `BackOff`, the exec-format `Error`) — anything else
falls through to a snake_cased version of the real `reason` string, which `cause_of` already
turns into a low-severity, still-real incident rather than silently dropping it (its own
module docstring: "a kind nobody has classified yet still becomes an incident").

    python -m kaval_collector.k8s_events              # one pass (make watch-events)
    python -m kaval_collector.k8s_events --every 15   # keep going, the deployed shape
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol

from kaval_shared import metrics
from kaval_shared.models import Signal
from kubernetes import client
from kubernetes.client.exceptions import ApiException
from sqlalchemy import select
from sqlalchemy.orm import Session

from kaval_collector import __version__
from kaval_collector.synthetic import DEMO_NAMESPACE, KUBERNETES

_REASON_TO_KIND = {
    "OOMKilled": "pod_oom_killed",
    "BackOff": "pod_back_off",
}


def _snake(name: str) -> str:
    """`FailedScheduling` -> `failed_scheduling`. A smaller copy of
    `kaval_agent.correlate._snake` — duplicated rather than imported for the same
    upstream/downstream reason `kaval_collector.k8s.load_config` gives."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    return re.sub(r"[^a-z0-9]+", "_", spaced.lower()).strip("_") or "unknown"


def kind_for(reason: str, message: str) -> str:
    if reason in _REASON_TO_KIND:
        return _REASON_TO_KIND[reason]
    if reason == "Error" and "exec format error" in message:
        return "container_exited"
    return _snake(reason)


def _last_written_count(session: Session, event_uid: str) -> int:
    value = session.scalar(
        select(Signal.value)
        .where(Signal.source == KUBERNETES, Signal.value.contains({"event_uid": event_uid}))
        .order_by(Signal.observed_at.desc())
        .limit(1)
    )
    if value is None:
        return 0
    count = value["count"]
    return int(count) if isinstance(count, (int, float, str)) else 0


def last_exit(pod: client.V1Pod) -> tuple[str | None, int | None]:
    """Why a pod's container last ended, as `(reason, exit_code)`: the most recent termination
    among its containers, from `lastState` (a restarted container) or `state` (one that is down
    right now). `(None, None)` when nothing has terminated."""
    best: client.V1ContainerStateTerminated | None = None
    for cs in (pod.status.container_statuses if pod.status else None) or []:
        for state in (cs.last_state, cs.state):
            term = state.terminated if state else None
            if term is None:
                continue
            if best is None or (term.finished_at or NEVER) > (best.finished_at or NEVER):
                best = term
    if best is None:
        return None, None
    return best.reason, best.exit_code


NEVER = datetime.min.replace(tzinfo=UTC)


class _HasEvents(Protocol):
    @property
    def items(self) -> list[client.CoreV1Event]: ...


class EventSource(Protocol):
    """The two calls `poll` makes. `client.CoreV1Api` satisfies it, and so does the small fake in
    the tests. Naming the call, not the whole class, is what lets a fake stand in for the real
    client now that the client library ships type hints (KAV-70)."""

    def list_namespaced_event(self, namespace: str, *, field_selector: str = "") -> _HasEvents: ...

    def read_namespaced_pod(self, name: str, namespace: str) -> client.V1Pod: ...


def poll(
    session: Session, v1: EventSource, namespace: str = DEMO_NAMESPACE, *,
    now: datetime | None = None,
) -> list[str]:
    """One pass: every `Warning` event on a `Pod` in `namespace` whose count has grown since
    the last signal written for it becomes one new signal. Pure side effect on `session`
    (one commit, at the end) — the loop in `main` is what makes repeated passes happen."""
    now = now or datetime.now(UTC)
    events = v1.list_namespaced_event(
        namespace, field_selector="type=Warning",
    ).items
    report: list[str] = []
    for event in events:
        obj = event.involved_object
        if obj is None or obj.kind != "Pod" or event.metadata.uid is None:
            continue
        uid = event.metadata.uid
        count = event.count or 1
        if count <= _last_written_count(session, uid):
            continue
        reason = event.reason or "Unknown"
        message = event.message or ""
        target = f"{namespace}/{obj.name}"
        kind = kind_for(reason, message)
        value: dict[str, object] = {
            "type": event.type, "reason": reason, "message": message, "count": count,
            "involvedObject": {"kind": obj.kind, "namespace": namespace, "name": obj.name},
            "event_uid": uid,
        }
        if kind == "pod_back_off" and obj.name:
            exit_reason, exit_code = _why_it_ended(v1, namespace, obj.name)
            if exit_reason is not None:
                value["last_exit_reason"], value["last_exit_code"] = exit_reason, exit_code
            if exit_reason == "OOMKilled":
                kind = "pod_oom_killed"
        observed_at = event.last_timestamp or event.event_time or event.first_timestamp or now
        session.add(Signal(
            source=KUBERNETES, kind=kind, target=target, value=value, observed_at=observed_at,
        ))
        report.append(f"{kind:28} {target}  count={count}")
    session.commit()
    return report


def _why_it_ended(v1: EventSource, namespace: str, pod_name: str) -> tuple[str | None, int | None]:
    """The pod's last exit reason, or nothing when it cannot be read (already replaced, no
    permission): the signal is then written as the plain `BackOff` it always was, never lost."""
    try:
        return last_exit(v1.read_namespaced_pod(pod_name, namespace))
    except ApiException:
        return None, None


def report(lines: Sequence[str]) -> list[str]:
    return list(lines) or ["nothing new"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m kaval_collector.k8s_events",
        description="Poll real Kubernetes events in one namespace and write new ones as "
                     "signal rows.",
    )
    parser.add_argument("--namespace", default=DEMO_NAMESPACE)
    parser.add_argument("--every", type=float, metavar="SECONDS",
                        help="keep running, one pass every SECONDS; default: one pass")
    parser.add_argument("--version", action="version", version=f"kaval-collector {__version__}")
    args = parser.parse_args(argv)

    from kaval_shared.db import get_engine  # only a real run needs the database

    from kaval_collector import k8s
    k8s.load_config()
    v1 = client.CoreV1Api()

    if args.every:
        metrics.serve("collector", interval=args.every, version=__version__)
    try:
        while True:
            with Session(get_engine()) as session:
                for line in report(poll(session, v1, args.namespace)):
                    print(line, flush=True)
            metrics.pass_ok("collector")
            if not args.every:
                return 0
            time.sleep(args.every)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
