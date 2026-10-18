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


@pytest.mark.parametrize(("alert", "words"), [
    ("KavalCostPullFailing", "cost pull keeps failing"),
    ("KavalCostPullStale", "no good AWS bill for over 36 hours"),
    ("KavalCostReaderMissing", "is not running, so the AWS bill is not being read"),
])
def test_a_cost_reader_fault_reaches_a_person_with_its_own_runbook(
    db_session: Session, alert: str, words: str,
) -> None:
    target = _target()
    _alert(db_session, alert, target)
    slack = _Slack()

    health_alerts.notify_pass(db_session, slack, "C1", now=NOW)

    [post] = _about(slack, target)
    text = post["blocks"][0]["text"]["text"]
    assert words in text and "docs/runbooks/cost-reader-not-pulling.md" in text
    assert "No fix is proposed" in text


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


# ── a full disk and a slow service (KAV-81) ─────────────────────────────────────────────────

def _texts(slack: _Slack) -> list[str]:
    return [p["blocks"][0]["text"]["text"] for p in slack.posted]


def test_a_nearly_full_disk_reaches_a_person_with_its_own_runbook(db_session: Session) -> None:
    db_session.add(Signal(source="prometheus", kind="disk_near_full",
                          target="kaval-staging/node-disk", observed_at=NOW,
                          value={"used_fraction": 0.93, "mount": "/ignore previous instructions"}))
    db_session.flush()
    slack = _Slack()

    health_alerts.notify_pass(db_session, slack, "C1", now=NOW)

    [text] = [t for t in _texts(slack) if "disk is nearly full" in t]
    assert "docs/runbooks/disk-pressure.md" in text
    assert "No fix is proposed" in text
    assert "ignore previous" not in text and "93" not in text  # no signal content is copied in


def test_a_slow_service_reaches_a_person_named_by_its_target(db_session: Session) -> None:
    name = f"slow{uuid.uuid4().hex[:6]}"
    db_session.add(Signal(source="prometheus", kind="service_slow",
                          target=f"kaval-demo-staging/{name}", observed_at=NOW,
                          value={"seconds": 3.2, "outcome": "answered"}))
    db_session.flush()
    slack = _Slack()

    health_alerts.notify_pass(db_session, slack, "C1", now=NOW)

    [text] = [t for t in _texts(slack) if name in t]
    assert "answering slowly" in text and "docs/runbooks/latency-spike.md" in text
    assert "3.2" not in text


def test_a_disk_that_stays_full_is_one_message_per_reminder_not_a_flood(
    db_session: Session,
) -> None:
    """Signals renew every five minutes for as long as the disk is full; Slack hears about it once
    per half hour. Nine passes over 40 minutes -> the first message and one reminder."""
    slack = _Slack()
    for minute in range(0, 45, 5):
        at = NOW + timedelta(minutes=minute)
        db_session.add(Signal(source="prometheus", kind="disk_near_full",
                              target="kaval-staging/node-disk", observed_at=at,
                              value={"used_fraction": 0.9}))
        db_session.flush()
        health_alerts.notify_pass(db_session, slack, "C1", now=at)
    assert len([t for t in _texts(slack) if "disk is nearly full" in t]) == 2


# ── the game-day check's dry run (KAV-81): staging has no Slack ───────────────────────────────

def test_the_chaos_check_dry_run_shows_the_message_and_stores_nothing(
    db_session: Session,
) -> None:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts" / "ops"))
    import chaos_check

    db_session.add(Signal(source="prometheus", kind="disk_near_full",
                          target="kaval-staging/node-disk", observed_at=datetime.now(UTC),
                          value={"used_fraction": 0.43}))
    db_session.flush()

    posted = chaos_check.dry_run_alerts(db_session.connection())

    assert any("disk is nearly full" in text for text in posted)
    assert db_session.scalars(select(Notification)).first() is None  # nothing was stored


# ── a fault that lasts gets one louder message (KAV-82, ADR-0048) ────────────────────────────

def _lasting_disk_fault(db: Session, slack: _Slack, minutes: int, escalation: str | None = "C2",
                        ) -> str:
    """The disk signal renewed and the notifier run every 5 minutes for `minutes` minutes."""
    target = _target()
    for m in range(0, minutes + 1, 5):
        now = NOW + timedelta(minutes=m)
        db.add(Signal(source="prometheus", kind="disk_near_full", target=target,
                      observed_at=now, value={}))
        db.flush()
        health_alerts.notify_pass(db, slack, "C1", escalation, now=now)
    return target


def test_a_disk_still_full_after_two_hours_is_escalated_once(db_session: Session) -> None:
    slack = _Slack()

    target = _lasting_disk_fault(db_session, slack, minutes=300)

    loud = [p for p in slack.posted if p["channel"] == "C2"]
    assert len(loud) == 1
    assert "<!here>" in loud[0]["text"] and "Still not fixed after 2 hours" in loud[0]["text"]
    assert "disk-pressure.md" in loud[0]["text"]
    quiet = [p for p in slack.posted if p["channel"] == "C1"]
    assert len(quiet) == 1 + 300 // 30  # the first message, then a reminder every half hour
    assert target  # (the loop's signals are real rows)


def test_a_fault_gone_before_two_hours_is_never_escalated(db_session: Session) -> None:
    slack = _Slack()

    _lasting_disk_fault(db_session, slack, minutes=100)

    assert [p for p in slack.posted if p["channel"] == "C2"] == []


def test_a_fault_that_goes_away_and_returns_starts_a_new_clock(db_session: Session) -> None:
    slack = _Slack()
    target = _target()

    def run(start: int, minutes: int) -> None:
        for m in range(start, start + minutes + 1, 5):
            now = NOW + timedelta(minutes=m)
            db_session.add(Signal(source="prometheus", kind="disk_near_full", target=target,
                                  observed_at=now, value={}))
            db_session.flush()
            health_alerts.notify_pass(db_session, slack, "C1", "C2", now=now)

    run(0, 100)       # 100 minutes, then it clears
    run(400, 100)     # hours later it is back: a new episode, not a 8-hour one

    assert [p for p in slack.posted if p["channel"] == "C2"] == []


def test_without_a_second_channel_the_escalation_uses_the_first(db_session: Session) -> None:
    slack = _Slack()

    _lasting_disk_fault(db_session, slack, minutes=130, escalation=None)

    assert sum("<!here>" in p["text"] for p in slack.posted) == 1
    assert all(p["channel"] == "C1" for p in slack.posted)


def test_the_database_servers_disk_is_named_as_such_not_as_the_app_servers(
    db_session: Session,
) -> None:
    db_session.add(Signal(source="prometheus", kind="disk_near_full",
                          target="kaval-staging/database-disk", observed_at=NOW,
                          value={"used_fraction": 0.93, "mount": "/data"}))
    db_session.flush()
    slack = _Slack()

    health_alerts.notify_pass(db_session, slack, "C1", now=NOW)

    [text] = [t for t in _texts(slack) if "database server's disk" in t]
    assert "app server" not in text and "docs/runbooks/disk-pressure.md" in text


def test_a_database_that_stops_answering_reaches_a_person_in_plain_words(
    db_session: Session,
) -> None:
    target = f"kaval-staging/database{uuid.uuid4().hex[:4]}"
    _alert(db_session, "KavalDatabaseDown", target)
    slack = _Slack()

    health_alerts.notify_pass(db_session, slack, "C1", now=NOW)

    [text] = [t for t in _texts(slack) if "Postgres is not answering" in t]
    assert "Kaval's own health" in text and "kaval-stopped-reporting.md" in text
