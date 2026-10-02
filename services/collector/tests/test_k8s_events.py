"""`kaval_collector.k8s_events`: the reason -> kind mapping (pure), and the count-based
de-duplication against a real database (`poll`'s whole reason for existing — KAV-48,
ADR-0022). The Kubernetes API itself is a tiny fake object shaped like
`CoreV1Api.list_namespaced_event`'s real return value, not a live cluster — `kaval_executor`'s
tests take the same approach for the same reason: this module's own logic, not the client
library, is what's under test.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from kaval_collector import k8s_events
from kaval_collector.synthetic import DEMO_NAMESPACE, KUBERNETES
from kaval_shared.models import Signal
from kubernetes import client
from sqlalchemy import select
from sqlalchemy.orm import Session

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def test_kind_for_known_reasons() -> None:
    assert k8s_events.kind_for("OOMKilled", "") == "pod_oom_killed"
    assert k8s_events.kind_for("BackOff", "") == "pod_back_off"
    assert k8s_events.kind_for("Error", "exec /bin/x: exec format error") == "container_exited"


def test_kind_for_unknown_reason_falls_back_to_snake_case() -> None:
    # Not silently dropped — kaval_agent.correlate.cause_of already turns an unrecognised
    # kind into a low-severity incident rather than ignoring it.
    assert k8s_events.kind_for("FailedScheduling", "") == "failed_scheduling"
    assert k8s_events.kind_for("Unhealthy", "") == "unhealthy"


@dataclass
class _EventList:
    items: list[client.CoreV1Event] = field(default_factory=list)


class _FakeCoreV1Api:
    """Stands in for `client.CoreV1Api` — `poll()` only ever calls one method on it."""

    def __init__(self, events: list[client.CoreV1Event]) -> None:
        self._events = events

    def list_namespaced_event(self, namespace: str, field_selector: str = "") -> _EventList:
        return _EventList(items=self._events)


def _event(
    *, uid: str | None = None, reason: str = "OOMKilled", message: str = "killed",
    count: int = 1, kind: str = "Pod", name: str = "checkout-7f9c4-x2klp",
    last_timestamp: datetime | None = NOW,
) -> client.CoreV1Event:
    return client.CoreV1Event(
        metadata=client.V1ObjectMeta(uid=uid or str(uuid.uuid4())),
        involved_object=client.V1ObjectReference(kind=kind, name=name),
        type="Warning", reason=reason, message=message, count=count,
        last_timestamp=last_timestamp,
    )


def test_poll_writes_one_signal_for_a_new_event(db_session: Session) -> None:
    target_name = f"checkout-{uuid.uuid4().hex[:8]}"
    event = _event(name=target_name)
    api = _FakeCoreV1Api([event])

    lines = k8s_events.poll(db_session, api, DEMO_NAMESPACE, now=NOW)

    assert len(lines) == 1 and "pod_oom_killed" in lines[0]
    rows = db_session.scalars(
        select(Signal).where(Signal.target == f"{DEMO_NAMESPACE}/{target_name}")
    ).all()
    assert len(rows) == 1
    assert rows[0].kind == "pod_oom_killed" and rows[0].value["event_uid"] == event.metadata.uid


def test_poll_is_idempotent_when_the_event_count_has_not_grown(db_session: Session) -> None:
    target_name = f"checkout-{uuid.uuid4().hex[:8]}"
    event = _event(name=target_name, reason="BackOff", count=3)
    api = _FakeCoreV1Api([event])

    first = k8s_events.poll(db_session, api, DEMO_NAMESPACE, now=NOW)
    second = k8s_events.poll(db_session, api, DEMO_NAMESPACE, now=NOW)  # same event, same count

    assert len(first) == 1 and second == []
    rows = db_session.scalars(
        select(Signal).where(Signal.target == f"{DEMO_NAMESPACE}/{target_name}")
    ).all()
    assert len(rows) == 1  # not two — the whole point of the count check


def test_poll_writes_a_new_signal_when_the_event_count_grows(db_session: Session) -> None:
    target_name = f"checkout-{uuid.uuid4().hex[:8]}"
    uid = str(uuid.uuid4())
    api = _FakeCoreV1Api([_event(uid=uid, name=target_name, reason="BackOff", count=2)])
    k8s_events.poll(db_session, api, DEMO_NAMESPACE, now=NOW)

    # The real event kept happening: Kubernetes bumps count on the same object, not a new one.
    api._events = [_event(uid=uid, name=target_name, reason="BackOff", count=5)]
    lines = k8s_events.poll(db_session, api, DEMO_NAMESPACE, now=NOW)

    assert len(lines) == 1 and "count=5" in lines[0]
    rows = db_session.scalars(
        select(Signal).where(Signal.target == f"{DEMO_NAMESPACE}/{target_name}")
        .order_by(Signal.observed_at)
    ).all()
    assert [r.value["count"] for r in rows] == [2, 5]


def test_poll_ignores_events_not_involving_a_pod(db_session: Session) -> None:
    api = _FakeCoreV1Api([_event(kind="Node", name="k3d-kaval-local-server-0")])
    assert k8s_events.poll(db_session, api, DEMO_NAMESPACE, now=NOW) == []


def test_poll_falls_back_to_now_when_the_event_carries_no_timestamp(db_session: Session) -> None:
    target_name = f"checkout-{uuid.uuid4().hex[:8]}"
    api = _FakeCoreV1Api([_event(name=target_name, last_timestamp=None)])
    k8s_events.poll(db_session, api, DEMO_NAMESPACE, now=NOW)
    row = db_session.scalar(
        select(Signal).where(Signal.target == f"{DEMO_NAMESPACE}/{target_name}")
    )
    assert row is not None and row.observed_at == NOW


def test_poll_writes_the_source_kubernetes_with_the_synthetic_vocabulary(
    db_session: Session,
) -> None:
    target_name = f"checkout-{uuid.uuid4().hex[:8]}"
    api = _FakeCoreV1Api([_event(name=target_name)])
    k8s_events.poll(db_session, api, DEMO_NAMESPACE, now=NOW)
    row = db_session.scalar(
        select(Signal).where(Signal.target == f"{DEMO_NAMESPACE}/{target_name}")
    )
    assert row is not None and row.source == KUBERNETES
