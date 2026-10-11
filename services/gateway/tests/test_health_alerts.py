"""Telling a person when Kaval itself has stopped (KAV-78, ADR-0042), against a real Postgres.
Slack is a small fake that records what would have been posted; the query, the de-duplication
and the "recorded only after the post succeeded" rule all run for real."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from kaval_gateway import health_alerts
from kaval_shared.models import Notification, Signal
from sqlalchemy import select
from sqlalchemy.orm import Session

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


class _Slack:
    def __init__(self, fail: bool = False) -> None:
        self.posted: list[dict[str, Any]] = []
        self.fail = fail

    def chat_postMessage(self, **kwargs: Any) -> dict[str, Any]:  # noqa: N802
        if self.fail:
            raise RuntimeError("slack is down")
        self.posted.append(kwargs)
        return {"ok": True}


def _target() -> str:
    return f"kaval-staging/svc{uuid.uuid4().hex[:6]}"


def _about(slack: _Slack, target: str) -> list[dict[str, Any]]:
    return [p for p in slack.posted if target.rsplit("/", 1)[-1] in p["text"]]


def _alert(db: Session, name: str, target: str, at: datetime = NOW) -> Signal:
    row = Signal(source="prometheus", kind="alert_firing", target=target, observed_at=at,
                 value={"alertname": name, "severity": "critical",
                        "service": target.rsplit("/", 1)[-1]})
    db.add(row)
    db.flush()
    return row


def test_a_stalled_service_is_posted_once_with_what_stopped_and_where_the_runbook_is(
    db_session: Session,
) -> None:
    target = _target()
    _alert(db_session, "KavalLoopStalled", target)
    slack = _Slack()

    health_alerts.notify_pass(db_session, slack, "C1", now=NOW)

    posts = _about(slack, target)
    assert len(posts) == 1
    text = posts[0]["blocks"][0]["text"]["text"]
    assert "stopped finishing its work" in text
    assert "docs/runbooks/kaval-stopped-reporting.md" in text
    assert "No fix is proposed" in text
    assert [b["type"] for b in posts[0]["blocks"]] == ["section"]  # nothing to approve: no buttons


def test_the_same_fault_is_not_repeated_until_the_reminder_period_passes(
    db_session: Session,
) -> None:
    target = _target()
    slack = _Slack()

    def mentions(now: datetime) -> int:
        _alert(db_session, "KavalTargetDown", target, at=now)  # the poller's renewal
        before = len(_about(slack, target))
        health_alerts.notify_pass(db_session, slack, "C1", now=now)
        return len(_about(slack, target)) - before

    assert mentions(NOW) == 1
    assert mentions(NOW + timedelta(minutes=5)) == 0
    assert mentions(NOW + timedelta(minutes=29)) == 0
    assert mentions(NOW + timedelta(minutes=31)) == 1   # a reminder, not silence forever


def test_the_always_firing_watchdog_never_reaches_a_person(db_session: Session) -> None:
    target = _target()
    _alert(db_session, "KavalWatchdog", target)
    slack = _Slack()

    health_alerts.notify_pass(db_session, slack, "C1", now=NOW)

    assert _about(slack, target) == []


def test_ordinary_trouble_is_not_announced_here(db_session: Session) -> None:
    target = f"kaval-demo/pod{uuid.uuid4().hex[:6]}"
    db_session.add_all([
        Signal(source="prometheus", kind="container_memory_near_limit", target=target,
               observed_at=NOW, value={"container": "c"}),
        Signal(source="prometheus", kind="alert_firing", target=target, observed_at=NOW,
               value={"alertname": "KubePodCrashLooping"}),
    ])
    db_session.flush()
    slack = _Slack()

    health_alerts.notify_pass(db_session, slack, "C1", now=NOW)

    assert _about(slack, target) == []


def test_the_pollers_own_checks_are_announced(db_session: Session) -> None:
    db_session.add(Signal(source="prometheus", kind="kaval_prometheus_unreachable",
                          target="kaval-staging/prometheus", observed_at=NOW,
                          value={"service": "prometheus"}))
    db_session.flush()
    slack = _Slack()

    health_alerts.notify_pass(db_session, slack, "C1", now=NOW)

    assert any("Prometheus cannot be reached" in p["blocks"][0]["text"]["text"]
               for p in slack.posted)


def test_a_fault_that_stopped_long_ago_is_not_announced(db_session: Session) -> None:
    target = _target()
    _alert(db_session, "KavalLoopStalled", target, at=NOW - timedelta(hours=2))
    slack = _Slack()

    health_alerts.notify_pass(db_session, slack, "C1", now=NOW)

    assert _about(slack, target) == []


def test_if_slack_fails_nothing_is_recorded_so_the_next_pass_tries_again(
    db_session: Session,
) -> None:
    target = _target()
    _alert(db_session, "KavalLoopStalled", target)
    key = f"KavalLoopStalled:{target}"

    with pytest.raises(RuntimeError):
        health_alerts.notify_pass(db_session, _Slack(fail=True), "C1", now=NOW)
    assert db_session.scalars(select(Notification).where(Notification.key == key)).first() is None

    retry = _Slack()
    health_alerts.notify_pass(db_session, retry, "C1", now=NOW)
    assert len(_about(retry, target)) == 1
    assert db_session.scalars(select(Notification).where(Notification.key == key)).one()
