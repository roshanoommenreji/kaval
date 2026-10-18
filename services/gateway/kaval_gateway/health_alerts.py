"""Tells a person when Kaval itself has stopped working (KAV-78, ADR-0042), and when a disk is
nearly full or a service has become slow (KAV-81, ADR-0047): the incidents that are for a person.

Prometheus decides that a service has gone quiet, and the collector's Prometheus poller writes
that as a `signal` row (`alert_firing` for a rule, `kaval_*` kinds for the poller's own checks).
This module turns those signals into one Slack message each, **straight from the signal table**.
It deliberately does not wait for the correlator to open an incident: if the correlator is the
thing that stopped, an alert routed through it would never arrive. The alert path may depend on
Prometheus, the poller, the database and this gateway, and on nothing it is reporting about.

One message per thing per reminder period. The signals repeat every few minutes while the fault
lasts (so the incident stays open); a message every few minutes would be noise, so `notification`
remembers what was sent and a reminder goes out only after `REMINDER`.

No buttons: there is nothing to approve. The message says what is wrong, and where the runbook is.
A fixed sentence per kind and a name taken from the signal's target: no number, word or text from
the signal itself is copied in (the rows are copied to staging with the rest of the database).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from kaval_shared import health
from kaval_shared.models import Notification, Signal
from sqlalchemy import select
from sqlalchemy.orm import Session

# How far back a signal still counts as "happening now": two renewals of the poller (300 s).
FRESH = timedelta(minutes=15)
# A fault that lasts is mentioned again after this long, not every five minutes.
REMINDER = timedelta(minutes=30)
# A fault still being reported after this long gets one louder message (KAV-82, ADR-0048). There
# is no Acknowledge button on these messages, so "nobody answered" is read from "still firing".
ESCALATE_AFTER = timedelta(hours=2)
# Reminders go out every REMINDER; a gap longer than this between two of them means the fault
# went away and came back, which is a new episode with a new clock.
EPISODE_GAP = 2 * REMINDER
# The rule that is always firing, to prove the rules are being evaluated. Not a fault.
WATCHDOG = "KavalWatchdog"
RUNBOOK = "docs/runbooks/kaval-stopped-reporting.md"
_RUNBOOKS = {
    "disk_near_full": "docs/runbooks/disk-pressure.md",
    "service_slow": "docs/runbooks/latency-spike.md",
    "KavalCostPullFailing": "docs/runbooks/cost-reader-not-pulling.md",
    "KavalCostPullStale": "docs/runbooks/cost-reader-not-pulling.md",
    "KavalCostReaderMissing": "docs/runbooks/cost-reader-not-pulling.md",
}

_WORDS = {
    "KavalLoopStalled": "has stopped finishing its work (it is running, but doing nothing)",
    "KavalTargetDown": "cannot be reached at all",
    "kaval_prometheus_unreachable": "Prometheus cannot be reached, so Kaval cannot see anything",
    "kaval_watchdog_missing": "Prometheus answers, but its alert rules are not running",
    "KavalDatabaseDown": "cannot be reached, or Postgres is not answering",
    "KavalDatabaseMonitorDown": "is not answering, so the database server's disk is not watched",
    "KavalCostPullFailing": "cannot read the AWS bill: the daily cost pull keeps failing",
    "KavalCostPullStale": "has had no good AWS bill for over 36 hours",
    "KavalCostReaderMissing": "is not running, so the AWS bill is not being read",
    "disk_near_full": "the app server's disk is nearly full",
    "service_slow": "is answering slowly (or not in time)",
}


def _name(signal: Signal) -> str:
    """The alert's name for a rule's signal, or the signal's kind for the poller's own."""
    if signal.kind == "alert_firing":
        return str(signal.value.get("alertname", signal.kind))
    return signal.kind


def _is_for_a_person(signal: Signal) -> bool:
    name = _name(signal)
    if name == WATCHDOG:
        return False
    return (name.startswith("Kaval") or health.is_health_cause(name)
            or name in health.PEOPLE_ONLY_SIGNALS)


def current(session: Session, now: datetime) -> dict[str, Signal]:
    """The latest fresh health signal for each distinct fault, keyed `<name>:<target>`."""
    rows = session.scalars(
        select(Signal).where(Signal.source == "prometheus", Signal.observed_at >= now - FRESH)
        .order_by(Signal.observed_at)
    )
    return {f"{_name(s)}:{s.target}": s for s in rows if _is_for_a_person(s)}


def _headline(signal: Signal) -> tuple[str, str]:
    """(emoji, sentence) for one signal. Only a fixed sentence and a service name: the text is
    stored (notification.message) and copied to staging with the rest of the database, so no
    signal text may ever go into it."""
    name = _name(signal)
    what = _WORDS.get(name, "is reporting a problem")
    service = signal.target.rsplit("/", 1)[-1]
    if name == "disk_near_full":
        if signal.target.endswith("/database-disk"):
            return ":warning:", "*Needs a person:* the database server's disk is nearly full."
        return ":warning:", f"*Needs a person:* {what}."
    if name == "service_slow":
        return ":warning:", f"*Needs a person:* `{service}` {what}."
    return ":rotating_light:", f"*Kaval's own health:* `{service}` {what}."


def message(signal: Signal) -> str:
    name = _name(signal)
    emoji, sentence = _headline(signal)
    lines = [f"{emoji} {sentence}"]
    runbook = _RUNBOOKS.get(name, RUNBOOK)
    lines.append(f"No fix is proposed for this; a person needs to look. Runbook: `{runbook}`")
    return "\n".join(lines)


def _episode_start(session: Session, key: str, now: datetime) -> datetime | None:
    """When the current run of reminders for `key` began: walk back through its messages until
    two are further apart than `EPISODE_GAP`."""
    times = list(session.scalars(
        select(Notification.created_at).where(Notification.key == key)
        .order_by(Notification.created_at.desc())
    ))
    if not times or now - times[0] >= EPISODE_GAP:
        return None
    start = times[0]
    for earlier in times[1:]:
        if start - earlier >= EPISODE_GAP:
            break
        start = earlier
    return start


def _escalate(
    session: Session, web_client: Any, key: str, signal: Signal, escalation_channel: str,
    now: datetime,
) -> bool:
    """One louder message per episode, once the fault has been reported for `ESCALATE_AFTER`."""
    started = _episode_start(session, key, now)
    if started is None or now - started < ESCALATE_AFTER:
        return False
    marker = f"{key}#escalated"
    done = session.scalar(
        select(Notification.created_at).where(Notification.key == marker)
        .order_by(Notification.created_at.desc()).limit(1)
    )
    if done is not None and done >= started:
        return False
    hours = int((now - started).total_seconds() // 3600)
    text = (f":rotating_light: <!here> Still not fixed after {hours} hours. "
            f"{_headline(signal)[1]} Runbook: `{_RUNBOOKS.get(_name(signal), RUNBOOK)}`")
    web_client.chat_postMessage(channel=escalation_channel, text=text)
    session.add(Notification(key=marker, message=text, created_at=now))
    session.commit()
    return True


def notify_pass(
    session: Session, web_client: Any, channel: str, escalation_channel: str | None = None,
    now: datetime | None = None,
) -> int:
    """One pass: post each fresh fault that was not already mentioned within `REMINDER`, and
    escalate the ones that have lasted `ESCALATE_AFTER`.
    Session-injectable, like `slack_chatops.notify_pass`: a test needs no Slack and no thread."""
    now = now or datetime.now(UTC)
    escalation_channel = escalation_channel or channel
    sent = 0
    for key, signal in current(session, now).items():
        if _escalate(session, web_client, key, signal, escalation_channel, now):
            sent += 1
        last = session.scalar(
            select(Notification.created_at).where(Notification.key == key)
            .order_by(Notification.created_at.desc()).limit(1)
        )
        if last is not None and now - last < REMINDER:
            continue
        text = message(signal)
        block = {"type": "section", "text": {"type": "mrkdwn", "text": text}}
        web_client.chat_postMessage(channel=channel, text=text.splitlines()[0], blocks=[block])
        # Recorded after the post: a Slack failure raises before this line, so the next pass
        # tries again rather than believing a person was told.
        session.add(Notification(key=key, message=text, created_at=now))
        session.commit()
        sent += 1
    return sent
