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
# The rule that is always firing, to prove the rules are being evaluated. Not a fault.
WATCHDOG = "KavalWatchdog"
RUNBOOK = "docs/runbooks/kaval-stopped-reporting.md"
_RUNBOOKS = {
    "disk_near_full": "docs/runbooks/disk-pressure.md",
    "service_slow": "docs/runbooks/latency-spike.md",
}

_WORDS = {
    "KavalLoopStalled": "has stopped finishing its work (it is running, but doing nothing)",
    "KavalTargetDown": "cannot be reached at all",
    "kaval_prometheus_unreachable": "Prometheus cannot be reached, so Kaval cannot see anything",
    "kaval_watchdog_missing": "Prometheus answers, but its alert rules are not running",
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


def message(signal: Signal) -> str:
    name = _name(signal)
    what = _WORDS.get(name, "is reporting a problem")
    service = signal.target.rsplit("/", 1)[-1]
    # Only a fixed sentence and a service name: this text is stored (notification.message) and
    # copied to staging with the rest of the database, so no signal text may ever go into it.
    if name == "disk_near_full":
        lines = [f":warning: *Needs a person:* {what}."]
    elif name == "service_slow":
        lines = [f":warning: *Needs a person:* `{service}` {what}."]
    else:
        lines = [f":rotating_light: *Kaval's own health:* `{service}` {what}."]
    runbook = _RUNBOOKS.get(name, RUNBOOK)
    lines.append(f"No fix is proposed for this; a person needs to look. Runbook: `{runbook}`")
    return "\n".join(lines)


def notify_pass(
    session: Session, web_client: Any, channel: str, now: datetime | None = None,
) -> int:
    """One pass: post each fresh fault that was not already mentioned within `REMINDER`.
    Session-injectable, like `slack_chatops.notify_pass`: a test needs no Slack and no thread."""
    now = now or datetime.now(UTC)
    sent = 0
    for key, signal in current(session, now).items():
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
