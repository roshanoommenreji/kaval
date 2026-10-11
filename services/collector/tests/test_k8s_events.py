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
from kubernetes.client.exceptions import ApiException
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
    """Stands in for `client.CoreV1Api` — `poll()` calls two methods on it."""

    def __init__(self, events: list[client.CoreV1Event],
                 pods: dict[str, client.V1Pod] | None = None) -> None:
        self._events = events
        self._pods = pods or {}
        self.pod_reads: list[str] = []

    def list_namespaced_event(self, namespace: str, field_selector: str = "") -> _EventList:
        return _EventList(items=self._events)

    def read_namespaced_pod(self, name: str, namespace: str) -> client.V1Pod:
        self.pod_reads.append(name)
        if name not in self._pods:
            raise ApiException(status=404)
        return self._pods[name]


def _terminated(reason: str, code: int, minute: int = 0) -> client.V1ContainerStateTerminated:
    return client.V1ContainerStateTerminated(
        reason=reason, exit_code=code, finished_at=NOW.replace(minute=minute))


def _pod(*, last: client.V1ContainerStateTerminated | None = None,
         now: client.V1ContainerStateTerminated | None = None) -> client.V1Pod:
    return client.V1Pod(status=client.V1PodStatus(container_statuses=[client.V1ContainerStatus(
        name="app", image="x", image_id="x", ready=False, restart_count=3,
        state=client.V1ContainerState(terminated=now),
        last_state=client.V1ContainerState(terminated=last),
    )]))


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


# ── KAV-80: a BackOff is labelled by why the container last ended ───────────────────────

def _signal(db_session: Session, name: str) -> Signal:
    row = db_session.scalar(select(Signal).where(Signal.target == f"{DEMO_NAMESPACE}/{name}"))
    assert row is not None
    return row


def test_a_back_off_for_a_pod_killed_for_memory_becomes_pod_oom_killed(
    db_session: Session,
) -> None:
    name = f"oomer-{uuid.uuid4().hex[:8]}"
    api = _FakeCoreV1Api([_event(name=name, reason="BackOff", count=4)],
                         {name: _pod(last=_terminated("OOMKilled", 137))})
    lines = k8s_events.poll(db_session, api, DEMO_NAMESPACE, now=NOW)
    assert "pod_oom_killed" in lines[0]
    row = _signal(db_session, name)
    assert row.kind == "pod_oom_killed"
    # The original event is still all there, plus the reason it was relabelled for.
    assert row.value["reason"] == "BackOff"
    assert row.value["last_exit_reason"] == "OOMKilled" and row.value["last_exit_code"] == 137


def test_a_back_off_for_a_program_that_crashed_on_its_own_stays_a_back_off(
    db_session: Session,
) -> None:
    name = f"badrelease-{uuid.uuid4().hex[:8]}"
    api = _FakeCoreV1Api([_event(name=name, reason="BackOff")],
                         {name: _pod(last=_terminated("Error", 1))})
    k8s_events.poll(db_session, api, DEMO_NAMESPACE, now=NOW)
    row = _signal(db_session, name)
    assert row.kind == "pod_back_off"
    assert row.value["last_exit_reason"] == "Error" and row.value["last_exit_code"] == 1


def test_a_back_off_for_a_pod_that_is_already_gone_is_still_recorded(db_session: Session) -> None:
    name = f"gone-{uuid.uuid4().hex[:8]}"
    api = _FakeCoreV1Api([_event(name=name, reason="BackOff")])  # the pod read gives 404
    k8s_events.poll(db_session, api, DEMO_NAMESPACE, now=NOW)
    row = _signal(db_session, name)
    assert row.kind == "pod_back_off" and "last_exit_reason" not in row.value


def test_only_a_back_off_costs_a_pod_read(db_session: Session) -> None:
    name = f"checkout-{uuid.uuid4().hex[:8]}"
    api = _FakeCoreV1Api([_event(name=name, reason="Unhealthy")])
    k8s_events.poll(db_session, api, DEMO_NAMESPACE, now=NOW)
    assert api.pod_reads == []


def test_last_exit_takes_the_most_recent_termination() -> None:
    pod = _pod(last=_terminated("OOMKilled", 137, minute=1), now=_terminated("Error", 1, minute=5))
    assert k8s_events.last_exit(pod) == ("Error", 1)
    assert k8s_events.last_exit(_pod()) == (None, None)
    assert k8s_events.last_exit(client.V1Pod()) == (None, None)
