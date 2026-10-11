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


# ── Kaval's own health: firing alerts and the poller's own checks (KAV-78, ADR-0042) ─────────

KAVAL_NS = "kaval-staging"


class _FakeAlerts:
    """Answers the two ALERTS questions `poll_alerts` asks."""

    def __init__(self, firing: list[Sample], watchdog: bool = True) -> None:
        self.firing, self.watchdog = firing, watchdog
        self.asked: list[str] = []

    def query(self, promql: str) -> list[Sample]:
        self.asked.append(promql)
        if prometheus.WATCHDOG in promql:
            return [Sample({"alertname": prometheus.WATCHDOG}, 1)] if self.watchdog else []
        return self.firing


def _alert(name: str, service: str, **extra: str) -> Sample:
    return Sample({"alertname": name, "service": service, "severity": "critical", **extra}, 1)


def _health_rows(db_session: Session) -> list[Signal]:
    return list(db_session.scalars(
        select(Signal).where(Signal.target.like(f"{KAVAL_NS}/%")).order_by(Signal.observed_at)
    ))


def test_a_firing_kaval_alert_becomes_the_alert_signal_the_correlator_already_knows(
    db_session: Session,
) -> None:
    service = f"executor-{uuid.uuid4().hex[:6]}"
    prom = _FakeAlerts([_alert("KavalLoopStalled", service)])

    lines = prometheus.poll_alerts(db_session, prom, KAVAL_NS, now=NOW)

    assert len(lines) == 1 and "KavalLoopStalled" in lines[0]
    row = db_session.scalars(select(Signal).where(Signal.target == f"{KAVAL_NS}/{service}")).one()
    assert (row.source, row.kind, row.observed_at) == (PROMETHEUS, "alert_firing", NOW)
    assert row.value == {"alertname": "KavalLoopStalled", "severity": "critical",
                         "service": service}
    # only Kaval's own alerts are asked for: a cluster-wide alert is not this poller's business
    assert any('kaval_health="true"' in q for q in prom.asked)


def test_an_alert_is_renewed_while_it_keeps_firing_but_not_every_pass(db_session: Session) -> None:
    service = f"agent-{uuid.uuid4().hex[:6]}"
    prom = _FakeAlerts([_alert("KavalTargetDown", service)])

    first = prometheus.poll_alerts(db_session, prom, KAVAL_NS, renew_seconds=300, now=NOW)
    soon = prometheus.poll_alerts(db_session, prom, KAVAL_NS, renew_seconds=300,
                                  now=NOW + timedelta(seconds=30))
    later = prometheus.poll_alerts(db_session, prom, KAVAL_NS, renew_seconds=300,
                                   now=NOW + timedelta(seconds=301))

    assert (len(first), len(soon), len(later)) == (1, 0, 1)
    assert len(db_session.scalars(
        select(Signal).where(Signal.target == f"{KAVAL_NS}/{service}")).all()) == 2


def test_two_different_faults_on_one_service_are_two_signals(db_session: Session) -> None:
    service = f"gateway-{uuid.uuid4().hex[:6]}"
    prom = _FakeAlerts([_alert("KavalLoopStalled", service), _alert("KavalTargetDown", service)])

    assert len(prometheus.poll_alerts(db_session, prom, KAVAL_NS, now=NOW)) == 2


def test_an_alert_with_no_service_is_skipped_not_guessed_at(db_session: Session) -> None:
    prom = _FakeAlerts([Sample({"alertname": "KavalLoopStalled"}, 1)])
    before = len(_health_rows(db_session))

    assert prometheus.poll_alerts(db_session, prom, KAVAL_NS, now=NOW) == []
    assert len(_health_rows(db_session)) == before


def test_the_watchdog_present_is_silence_and_missing_is_a_signal_once_per_renewal(
    db_session: Session,
) -> None:
    before = len(_health_rows(db_session))
    quiet = prometheus.poll_alerts(db_session, _FakeAlerts([], watchdog=True), KAVAL_NS, now=NOW)
    assert quiet == [] and len(_health_rows(db_session)) == before  # the negative case

    gone = _FakeAlerts([], watchdog=False)
    first = prometheus.poll_alerts(db_session, gone, KAVAL_NS, renew_seconds=300, now=NOW)
    again = prometheus.poll_alerts(db_session, gone, KAVAL_NS, renew_seconds=300,
                                   now=NOW + timedelta(seconds=30))

    assert len(first) == 1 and again == []
    row = _health_rows(db_session)[-1]
    assert (row.kind, row.target) == (prometheus.WATCHDOG_MISSING, f"{KAVAL_NS}/prometheus")


def test_a_freshly_started_prometheus_is_given_time_to_evaluate_its_rules(
    db_session: Session,
) -> None:
    """Found live on staging, twice. A restarted Prometheus answers at once but its watchdog is
    absent for up to ~83 s. Three passes of 30 s fit inside that, so the grace is in time."""
    health = prometheus.Health()
    fresh = _FakeAlerts([], watchdog=False)
    before = len(_health_rows(db_session))

    for seconds in (0, 30, 60, 90, 120, 149):   # five passes and more, all inside the grace
        assert prometheus.poll_alerts(db_session, fresh, KAVAL_NS, health=health,
                                      now=NOW + timedelta(seconds=seconds)) == []
    assert len(_health_rows(db_session)) == before

    late = prometheus.poll_alerts(db_session, fresh, KAVAL_NS, health=health,
                                  now=NOW + prometheus.WATCHDOG_GRACE)
    assert len(late) == 1                                   # absent for the whole grace: real

    # once the rules are running, the clock starts again
    prometheus.poll_alerts(db_session, _FakeAlerts([], watchdog=True), KAVAL_NS, health=health,
                           now=NOW + timedelta(seconds=200))
    assert health.watchdog_missing_since is None


def test_unreachable_is_written_to_the_database_and_renewed_while_it_lasts(
    db_session: Session,
) -> None:
    first = prometheus.record_unreachable(db_session, KAVAL_NS, renew_seconds=300, now=NOW)
    soon = prometheus.record_unreachable(db_session, KAVAL_NS, renew_seconds=300,
                                         now=NOW + timedelta(seconds=60))
    later = prometheus.record_unreachable(db_session, KAVAL_NS, renew_seconds=300,
                                          now=NOW + timedelta(seconds=400))

    assert (len(first), len(soon), len(later)) == (1, 0, 1)
    kinds = {r.kind for r in _health_rows(db_session)}
    assert prometheus.UNREACHABLE in kinds


def test_main_raises_unreachable_only_after_three_misses_in_a_row(
    db_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One miss is a restart; the third in a row is an outage. Drives the real `main` loop."""
    answers = [False, False, True, False, False, False]  # per pass; True = Prometheus answers
    state = {"pass": -1}
    written: list[str] = []

    class _Flaky:
        def __init__(self, _url: str) -> None: ...

        def query(self, promql: str) -> list[Sample]:
            if "container_memory_working_set_bytes" in promql:  # the first question of a pass
                state["pass"] += 1
            if not answers[state["pass"]]:
                raise PrometheusError("down")
            watchdog = [Sample({"alertname": prometheus.WATCHDOG}, 1)]
            return watchdog if prometheus.WATCHDOG in promql else []

    def sleep(_seconds: float) -> None:
        if state["pass"] >= len(answers) - 1:
            raise KeyboardInterrupt  # the last scripted pass is done

    monkeypatch.setattr(prometheus, "HttpPrometheus", _Flaky)
    monkeypatch.setattr(prometheus, "record_unreachable",
                        lambda session, ns, **kw: written.append(ns) or [])  # type: ignore[func-returns-value]
    monkeypatch.setattr("time.sleep", sleep)
    monkeypatch.setenv("KAVAL_METRICS_PORT", "0")

    prometheus.main(["--every", "1", "--kaval-namespace", KAVAL_NS])

    # miss, miss, ok (resets the count), miss, miss, miss: only the final miss is the third in a row
    assert written == [KAVAL_NS]
