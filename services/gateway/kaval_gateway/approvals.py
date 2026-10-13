"""Approvals that nobody answers (KAV-82, ADR-0048): a reminder, then a louder second message,
then an expiry. Real on-call practice, not an open-ended wait.

Why an expiry at all: a proposal is a judgement about how things were when it was written. The
longer it sits, the less that judgement is worth, and a click a day later would act on a
cluster the proposal never saw. So a request that nobody answers within `Timing.expire` stops
being approvable. Three rules keep that honest:

* **The refusal is where the decision is written** (`decisions.record_decision` asks
  `is_expired`), not here. This module only *tells people*. If the gateway's notifier is down,
  a late click is still refused: the rule is computed from the action's own age and needs no
  stored state.
* **Escalation is a message, never a permission.** Nothing here approves, extends or re-opens
  anything. An expired request is final; a fresh proposal is needed.
* **Written down only after Slack accepted the message** (same rule as `health_alerts`), so a
  Slack outage means the next pass tries again instead of believing a person was told.

The steps are remembered in the `notification` table (`approval-reminder:<id>`,
`approval-escalated:<id>`, `approval-expired:<id>`), one row each, so no pass repeats a message.
Messages are fixed sentences plus the action's own type and target; no model text is copied in.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from kaval_shared.models import Action, Notification, PolicyClass
from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger("kaval_gateway.approvals")

# Roshan's slowest answers so far were 74 s twice and 273 s once; these leave room for a person
# who is asleep without leaving a stale request open for days.
DEFAULT_REMINDER_MINUTES = 15.0
DEFAULT_ESCALATE_MINUTES = 30.0
DEFAULT_EXPIRE_MINUTES = 240.0


@dataclass(frozen=True)
class Timing:
    reminder: timedelta
    escalate: timedelta
    expire: timedelta

    @classmethod
    def from_env(cls) -> Timing:
        """Read `APPROVAL_{REMINDER,ESCALATE,EXPIRE}_MINUTES`. Anything that is not a number, or
        that does not satisfy 0 < reminder < escalate < expire, falls back to the defaults for
        all three: a half-valid set could expire a request before it ever warned anyone."""
        raw = [
            os.environ.get("APPROVAL_REMINDER_MINUTES"),
            os.environ.get("APPROVAL_ESCALATE_MINUTES"),
            os.environ.get("APPROVAL_EXPIRE_MINUTES"),
        ]
        defaults = [DEFAULT_REMINDER_MINUTES, DEFAULT_ESCALATE_MINUTES, DEFAULT_EXPIRE_MINUTES]
        try:
            minutes = [
                float(r) if r not in (None, "") else d
                for r, d in zip(raw, defaults, strict=True)
            ]
        except ValueError:
            minutes = []
        finite = all(m == m and m != float("inf") for m in minutes)  # no NaN, no infinity
        if len(minutes) != 3 or not finite or not 0 < minutes[0] < minutes[1] < minutes[2]:
            if any(r not in (None, "") for r in raw):
                logger.warning("approvals: APPROVAL_*_MINUTES unusable %s, using defaults", raw)
            minutes = defaults
        return cls(*(timedelta(minutes=m) for m in minutes))


def _age(action: Action, now: datetime) -> timedelta:
    return now - action.created_at


def is_expired(action: Action, now: datetime | None = None, timing: Timing | None = None) -> bool:
    """True for an `ask`-class action nobody decided, older than the expiry. A decided action is
    never "expired" (its decision stands, or the executor's own age check applies), and `never`
    and `auto` actions are not waiting on anybody."""
    if action.policy_class != PolicyClass.ask or action.decision is not None:
        return False
    timing = timing or Timing.from_env()
    return _age(action, now or datetime.now(UTC)) >= timing.expire


def waiting(session: Session, told_only: bool = True) -> list[Action]:
    """Every `ask`-class action nobody has decided that a person has been told about.
    `told_only=False` also lists the ones Slack never posted (staging has no Slack): only the
    staging dry run (`scripts/ops/approvals_drill.py`) asks for that."""
    query = (
        select(Action)
        .where(Action.policy_class == PolicyClass.ask)
        .where(~Action.decision.has())
        .order_by(Action.created_at)
    )
    if told_only:
        query = query.where(Action.slack_notified_at.is_not(None))
    return list(session.scalars(query))


def _minutes(delta: timedelta) -> int:
    return max(int(delta.total_seconds() // 60), 0)


def _what(action: Action) -> str:
    return f"*{action.type}* on `{action.target}`"


def _reminder_text(action: Action, now: datetime, timing: Timing) -> str:
    left = timing.expire - _age(action, now)
    return (f":hourglass_flowing_sand: Still waiting for an answer: {_what(action)}, proposed "
            f"{_minutes(_age(action, now))} minutes ago. It expires in {_minutes(left)} minutes.")


def _escalation_text(action: Action, now: datetime, timing: Timing) -> str:
    left = timing.expire - _age(action, now)
    return (f":rotating_light: <!here> Nobody has answered for {_minutes(_age(action, now))} "
            f"minutes: {_what(action)}. It expires in {_minutes(left)} minutes and nothing "
            "will happen until someone approves or denies it.")


def _expired_text(action: Action) -> str:
    return (f":no_entry: Expired with no answer: {_what(action)}. Nothing was done. A request "
            "this old is no longer trusted; if the problem is still there, a new proposal is "
            "needed.")


def _remember(session: Session, key: str, text: str, now: datetime) -> None:
    session.add(Notification(key=key, message=text, created_at=now))
    session.commit()


def _strip_buttons(web_client: Any, channel: str, action: Action, text: str) -> None:
    """Take the buttons off the original message. Best effort: a refused late click is the real
    protection, so a failure here is logged and the expiry message still stands."""
    if not action.slack_ts:
        return
    try:
        block = {"type": "section", "text": {"type": "mrkdwn", "text": text}}
        web_client.chat_update(channel=channel, ts=action.slack_ts, text=text, blocks=[block])
    except Exception:  # noqa: BLE001 - see docstring
        logger.exception("approvals: could not remove the buttons from %s", action.slack_ts)


def notify_pass(
    session: Session, web_client: Any, channel: str, escalation_channel: str | None = None,
    now: datetime | None = None, timing: Timing | None = None, told_only: bool = True,
) -> int:
    """One pass: for each waiting request, send the one message it is due and has not had.

    Only the latest due step is sent (a gateway that was down for five hours says "expired",
    not three late messages in a row). Returns how many messages went out."""
    now = now or datetime.now(UTC)
    timing = timing or Timing.from_env()
    escalation_channel = escalation_channel or channel
    sent_keys = set(session.scalars(
        select(Notification.key).where(Notification.key.like("approval-%"))
    ))
    sent = 0
    for action in waiting(session, told_only):
        age = _age(action, now)
        reminded = f"approval-reminder:{action.id}"
        escalated = f"approval-escalated:{action.id}"
        expired = f"approval-expired:{action.id}"
        thread = {"thread_ts": action.slack_ts} if action.slack_ts else {}
        if expired in sent_keys:
            continue
        if age >= timing.expire:
            text = _expired_text(action)
            web_client.chat_postMessage(channel=channel, text=text, **thread)
            _strip_buttons(web_client, channel, action, text)
            _remember(session, expired, text, now)
        elif age >= timing.escalate:
            if escalated in sent_keys:
                continue
            text = _escalation_text(action, now, timing)
            web_client.chat_postMessage(channel=escalation_channel, text=text)
            _remember(session, escalated, text, now)
        elif age >= timing.reminder:
            if reminded in sent_keys or escalated in sent_keys:
                continue
            text = _reminder_text(action, now, timing)
            web_client.chat_postMessage(channel=channel, text=text, **thread)
            _remember(session, reminded, text, now)
        else:
            continue
        sent += 1
    return sent
