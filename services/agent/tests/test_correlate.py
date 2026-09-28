"""Correlation: the synthetic scenarios become the incidents a person would call them.

No __init__.py here, like the other services' tests: a second package named `tests` would
collide with services/shared/tests under pytest's default import mode.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest
from kaval_agent import correlate as c
from kaval_collector import synthetic
from kaval_shared.models import Incident, IncidentSignal, Severity, Signal
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

AT = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def run(scenario: str, at: datetime = AT, seed: int = 1) -> list[Signal]:
    """A scenario's signals with ids, as they'd come back from the database."""
    signals = synthetic.generate(scenario, at=at, seed=seed)
    for s in signals:
        s.id = uuid.uuid4()
    return signals


def signal(kind: str, at: datetime, target: str = "kaval-demo/checkout-bcdfg-hjklm",
           source: str = synthetic.KUBERNETES, **value: object) -> Signal:
    return Signal(id=uuid.uuid4(), source=source, kind=kind, target=target,
                  value=dict(value), observed_at=at)


def only(plan: c.Plan) -> c.NewIncident:
    assert len(plan.opened) == 1, plan.opened
    return plan.opened[0]


@pytest.mark.parametrize(("pod", "want"), [
    ("checkout-7f9c4d8b6-x2klp", "checkout"),   # Deployment
    ("checkout-bcdfg-hjklm", "checkout"),       # the synthetic generator's shape
    ("node-exporter-x2klp", "node-exporter"),   # DaemonSet
    ("postgres-0", "postgres"),                 # StatefulSet
    ("cache-redis", "cache-redis"),             # no generated suffix: kept whole
])
def test_pod_names_reduce_to_their_workload(pod: str, want: str) -> None:
    assert c.workload(pod) == want


def test_oom_crashloop_is_one_high_incident_named_for_the_kill() -> None:
    signals = run("oom-crashloop")
    incident = only(c.plan(signals, [], AT + timedelta(minutes=1)))
    assert incident.fingerprint == "oom_killed:k8s:kaval-demo/checkout"
    assert incident.severity is Severity.high
    assert len(incident.signals) == len(signals) == 6
    assert incident.opened_at == signals[0].observed_at  # the memory warning, first
    assert incident.closed_at is None


def test_exec_format_is_named_for_the_architecture_clue() -> None:
    incident = only(c.plan(run("exec-format"), [], AT + timedelta(minutes=1)))
    assert incident.fingerprint == "exec_format:k8s:kaval-demo/gateway"
    assert incident.severity is Severity.high


def test_cost_spike_opens_for_the_spike_day_only() -> None:
    days = run("cost-spike")
    incident = only(c.plan(days, [], AT))
    assert incident.fingerprint == "cost_spike:aws:ec2/ap-south-1"
    assert incident.signals == [days[-1]]  # six normal days are not a problem
    assert incident.severity is Severity.medium


def test_a_normal_week_of_cost_opens_nothing() -> None:
    days = run("cost-spike")[:-1]
    assert c.plan(days, [], AT).empty


def test_a_spike_needs_a_baseline() -> None:
    # Two days of history aren't enough to call the third a spike.
    days = run("cost-spike")[-3:]
    assert c.plan(days, [], AT).empty


def test_idle_volume_is_low_severity_on_the_volume() -> None:
    signals = run("idle-volume")
    incident = only(c.plan(signals, [], AT + timedelta(minutes=2)))
    assert incident.fingerprint == f"idle_volume:aws:{signals[0].target}"
    assert incident.severity is Severity.low


def test_a_restarted_pod_keeps_its_fingerprint() -> None:
    # Two seeds, two pod names, one workload, minutes apart: still one incident.
    a, b = run("oom-crashloop", seed=1), run("oom-crashloop", AT + timedelta(minutes=5), seed=2)
    assert a[0].target != b[0].target
    incident = only(c.plan([*a, *b], [], AT + timedelta(minutes=6)))
    assert len(incident.signals) == 12


def test_a_recurrence_after_the_quiet_window_is_a_new_incident_same_fingerprint() -> None:
    first = run("oom-crashloop")
    again = run("oom-crashloop", AT + timedelta(hours=1), seed=2)
    result = c.plan([*first, *again], [], AT + timedelta(hours=1, minutes=1))
    assert [n.fingerprint for n in result.opened] == ["oom_killed:k8s:kaval-demo/checkout"] * 2
    closed, still_open = result.opened
    assert closed.closed_at == first[-1].observed_at + c.QUIET["k8s"]
    assert still_open.closed_at is None


def test_a_new_group_waits_before_it_opens() -> None:
    now = AT
    signals = [signal("pod_back_off", now - timedelta(seconds=30), reason="BackOff")]
    result = c.plan(signals, [], now)
    assert result.empty and result.waiting == 1
    assert only(c.plan(signals, [], now + timedelta(seconds=31))).fingerprint == \
        "crashloop:k8s:kaval-demo/checkout"


def test_new_signals_join_the_open_incident_for_their_subject() -> None:
    open_ = c.OpenIncident(uuid.uuid4(), "oom_killed:k8s:kaval-demo/checkout", AT)
    late = signal("pod_back_off", AT + timedelta(minutes=2), reason="BackOff")
    result = c.plan([late], [open_], AT + timedelta(minutes=3))
    assert result.attached == {open_.id: [late]}
    assert not result.opened and not result.closed


def test_an_open_incident_closes_once_quiet() -> None:
    open_ = c.OpenIncident(uuid.uuid4(), "oom_killed:k8s:kaval-demo/checkout", AT)
    assert c.plan([], [open_], AT + timedelta(minutes=14)).empty
    assert c.plan([], [open_], AT + timedelta(minutes=15)).closed == {
        open_.id: AT + c.QUIET["k8s"]}


def test_aws_incidents_get_a_daily_sized_quiet_window() -> None:
    open_ = c.OpenIncident(uuid.uuid4(), "idle_volume:aws:ebs/vol-0abc", AT)
    assert c.plan([], [open_], AT + timedelta(hours=47)).empty


def test_an_unknown_kind_still_becomes_a_low_incident() -> None:
    incident = only(c.plan([signal("NodeDiskPressure", AT)], [], AT + timedelta(minutes=2)))
    assert incident.fingerprint == "node_disk_pressure:k8s:kaval-demo/checkout"
    assert incident.severity is Severity.low


def test_a_resolved_alert_is_not_a_problem() -> None:
    s = signal("alert_firing", AT, source=synthetic.ALERTMANAGER, status="resolved",
               alertname="KubePodCrashLooping")
    assert c.plan([s], [], AT + timedelta(minutes=2)).empty


def test_report_says_nothing_new_when_there_is_nothing() -> None:
    assert c.report(c.Plan()) == ["nothing new"]
    assert "lock" in c.report(None)[0]


# ── Against real Postgres (skipped without one; CI requires it) ──────────────────────────


def _mine(db: Session, signals: Sequence[Signal]) -> list[Incident]:
    ids = [s.id for s in signals]
    return list(db.scalars(
        select(Incident).join(IncidentSignal).where(IncidentSignal.signal_id.in_(ids))
        .distinct()
    ).all())


def test_a_pass_writes_the_incident_and_a_second_pass_changes_nothing(db_session: Session) -> None:
    now = datetime.now(UTC)
    signals = synthetic.generate("oom-crashloop", at=now, seed=11)
    synthetic.emit(db_session, signals)

    first = c.correlate(db_session, now + timedelta(minutes=1))
    assert first is not None
    [incident] = _mine(db_session, signals)
    assert incident.fingerprint == "oom_killed:k8s:kaval-demo/checkout"
    assert {s.id for s in incident.signals} == {s.id for s in signals}

    second = c.correlate(db_session, now + timedelta(minutes=2))
    assert second is not None
    assert not second.opened and not second.attached
    assert len(_mine(db_session, signals)) == 1


def test_a_second_correlator_is_refused_while_one_runs(
    db_session: Session, migrated_engine: Engine
) -> None:
    # db_session's transaction takes the lock; a second connection must not get it.
    assert db_session.scalar(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": c.LOCK_KEY})
    with Session(migrated_engine) as other:
        assert c.correlate(other) is None
