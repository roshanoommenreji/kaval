"""`kaval_collector.prometheus`: reading Prometheus's answer, the threshold test (both pure), and
the renew-while-it-persists de-duplication against a real database (KAV-75, ADR-0040).
Prometheus itself is a small fake with the one method `poll` calls — this module's own logic,
not a live server, is what is under test; the live proof is Lab 43.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from kaval_collector import prometheus
from kaval_collector.prometheus import PrometheusError, Sample
from kaval_collector.synthetic import DEMO_NAMESPACE, PROMETHEUS
from kaval_shared.models import Signal
from sqlalchemy import select
from sqlalchemy.orm import Session

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
MIB = 1024 * 1024


def _sample(pod: str, container: str, value: float) -> Sample:
    return Sample(labels={"pod": pod, "container": container, "namespace": DEMO_NAMESPACE},
                  value=value)


class _FakePrometheus:
    """Answers by which metric the query names; records what was asked."""

    def __init__(self, usage: list[Sample], limits: list[Sample]) -> None:
        self.usage, self.limits = usage, limits
        self.asked: list[str] = []

    def query(self, promql: str) -> list[Sample]:
        self.asked.append(promql)
        return self.limits if "container_spec_memory_limit_bytes" in promql else self.usage


def test_parse_result_reads_a_vector() -> None:
    body = {"status": "success", "data": {"resultType": "vector", "result": [
        {"metric": {"pod": "a", "container": "c"}, "value": [1.7e9, "123.5"]},
        {"metric": {"pod": "b"}, "value": [1.7e9]},  # malformed pair, skipped
    ]}}
    assert prometheus.parse_result(body) == [Sample({"pod": "a", "container": "c"}, 123.5)]


@pytest.mark.parametrize("body", [
    {"status": "error", "errorType": "bad_data", "error": "parse error"},
    {"status": "success", "data": {"resultType": "matrix", "result": []}},
    "not even an object",
])
def test_parse_result_refuses_anything_that_is_not_a_vector(body: object) -> None:
    with pytest.raises(PrometheusError):
        prometheus.parse_result(body)


def test_only_http_urls_are_accepted() -> None:
    with pytest.raises(ValueError):
        prometheus.HttpPrometheus("file:///etc/passwd")


def test_near_limit_applies_the_threshold_and_skips_containers_without_a_limit() -> None:
    usage = [
        _sample("hot", "app", 240 * MIB),       # 94% of 256Mi: yes
        _sample("edge", "app", 0.9 * 256 * MIB),  # exactly at the line: yes
        _sample("cool", "app", 100 * MIB),      # 39%: no
        _sample("nolimit", "app", 900 * MIB),   # no limit series at all: not nameable
        _sample("zero", "app", 900 * MIB),      # limit 0 means unlimited: not nameable
    ]
    limits = [_sample(p, "app", 256 * MIB) for p in ("hot", "edge", "cool")]
    limits.append(_sample("zero", "app", 0))
    found = prometheus.near_limit(usage, limits, 0.9)
    assert [(pod, used > 0) for pod, _, used, _ in found] == [("edge", True), ("hot", True)]


def test_poll_writes_the_shape_the_agent_and_the_golden_evals_already_use(
    db_session: Session,
) -> None:
    pod = f"checkout-{uuid.uuid4().hex[:8]}"
    prom = _FakePrometheus([_sample(pod, "checkout", 245 * MIB)],
                           [_sample(pod, "checkout", 256 * MIB)])

    lines = prometheus.poll(db_session, prom, DEMO_NAMESPACE, now=NOW)

    assert len(lines) == 1 and "container_memory_near_limit" in lines[0] and "96%" in lines[0]
    row = db_session.scalars(select(Signal).where(Signal.target == f"{DEMO_NAMESPACE}/{pod}")).one()
    assert (row.source, row.kind, row.observed_at) == (PROMETHEUS, prometheus.KIND, NOW)
    assert row.value == {
        "metric": "container_memory_working_set_bytes", "container": "checkout",
        "value_bytes": 245 * MIB, "limit_bytes": 256 * MIB,
    }
    assert all(f'namespace="{DEMO_NAMESPACE}"' in q for q in prom.asked)


def test_poll_renews_while_the_container_stays_near_its_limit_but_not_every_pass(
    db_session: Session,
) -> None:
    pod = f"checkout-{uuid.uuid4().hex[:8]}"
    prom = _FakePrometheus([_sample(pod, "checkout", 250 * MIB)],
                           [_sample(pod, "checkout", 256 * MIB)])
    target = f"{DEMO_NAMESPACE}/{pod}"

    first = prometheus.poll(db_session, prom, DEMO_NAMESPACE, renew_seconds=300, now=NOW)
    soon = prometheus.poll(db_session, prom, DEMO_NAMESPACE, renew_seconds=300,
                           now=NOW + timedelta(seconds=30))
    later = prometheus.poll(db_session, prom, DEMO_NAMESPACE, renew_seconds=300,
                            now=NOW + timedelta(seconds=301))

    assert (len(first), soon, len(later)) == (1, [], 1)
    rows = db_session.scalars(select(Signal).where(Signal.target == target)).all()
    assert len(rows) == 2  # not 3 (every pass) and not 1 (the incident would close at 15 min)


def test_poll_writes_nothing_when_nothing_is_near_its_limit(db_session: Session) -> None:
    pod = f"checkout-{uuid.uuid4().hex[:8]}"
    prom = _FakePrometheus([_sample(pod, "checkout", 50 * MIB)],
                           [_sample(pod, "checkout", 256 * MIB)])
    assert prometheus.poll(db_session, prom, DEMO_NAMESPACE, now=NOW) == []
    assert prometheus.report([]) == ["nothing new"]
    assert db_session.scalars(
        select(Signal).where(Signal.target == f"{DEMO_NAMESPACE}/{pod}")
    ).all() == []
