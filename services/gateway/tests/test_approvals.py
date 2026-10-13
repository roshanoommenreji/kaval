"""Approvals that nobody answers (KAV-82, ADR-0048), against a real Postgres. Slack is a small
fake that records what would have been posted; the query, the one-message-per-step memory and the
refusal of a late click all run for real."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from kaval_gateway import approvals
from kaval_gateway.decisions import DecisionOutcome, record_decision
from kaval_shared.models import (
    Action,
    BlastRadius,
    Decision,
    Incident,
    Notification,
    PolicyClass,
    Proposal,
    RiskLevel,
    Severity,
    Signal,
    Verdict,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

PROPOSED = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)  # long before any real "now" a test runs at
TIMING = approvals.Timing(
    reminder=timedelta(minutes=15), escalate=timedelta(minutes=30), expire=timedelta(minutes=240),
)


def at(minutes: float) -> datetime:
    return PROPOSED + timedelta(minutes=minutes)


class _Slack:
    def __init__(self, fail: bool = False, fail_update: bool = False) -> None:
        self.posted: list[dict[str, Any]] = []
        self.updated: list[dict[str, Any]] = []
        self.fail = fail
        self.fail_update = fail_update

    def chat_postMessage(self, **kwargs: Any) -> dict[str, Any]:  # noqa: N802
        if self.fail:
            raise RuntimeError("slack is down")
        self.posted.append(kwargs)
        return {"ok": True, "ts": "111.222"}

    def chat_update(self, **kwargs: Any) -> dict[str, Any]:
        if self.fail_update:
            raise RuntimeError("cannot edit")
        self.updated.append(kwargs)
        return {"ok": True}


def _action(
    db: Session, *, policy_class: PolicyClass = PolicyClass.ask, notified: bool = True,
    decided: bool = False, ts: str | None = "999.000",
) -> Action:
    target = f"test/{uuid.uuid4().hex[:8]}"
    signal = Signal(source="kubernetes", kind="pod_back_off", target=target, value={},
                    observed_at=PROPOSED)
    incident = Incident(fingerprint=f"test-{uuid.uuid4()}", severity=Severity.high,
                        opened_at=PROPOSED, signals=[signal])
    proposal = Proposal(incident=incident, summary="restart it", root_cause="oom",
                        confidence=0.8, risk=RiskLevel.low, model="m", tokens_in=1,
                        tokens_out=1, cost_usd=Decimal("0"), created_at=PROPOSED)
    action = Action(proposal=proposal, type="restart_pod", target=target, params={},
                    reversible=True, blast_radius=BlastRadius.pod, policy_class=policy_class,
                    created_at=PROPOSED, slack_notified_at=PROPOSED if notified else None,
                    slack_ts=ts)
    db.add_all([incident, proposal, action])
    db.flush()
    if decided:
        db.add(Decision(action_id=action.id, verdict=Verdict.approved, actor="someone",
                        decided_at=PROPOSED))
        db.flush()
    db.refresh(action)
    return action


def _notes(db: Session, action: Action) -> list[str]:
    return sorted(db.scalars(
        select(Notification.key).where(Notification.key.like(f"approval-%:{action.id}"))
    ))


# ── the three steps ─────────────────────────────────────────────────────────────────────────

def test_nothing_is_sent_before_the_reminder_is_due(db_session: Session) -> None:
    action = _action(db_session)
    slack = _Slack()

    assert approvals.notify_pass(db_session, slack, "C1", now=at(14), timing=TIMING) == 0

    assert slack.posted == [] and _notes(db_session, action) == []


def test_a_reminder_goes_in_the_thread_once(db_session: Session) -> None:
    action = _action(db_session)
    slack = _Slack()

    assert approvals.notify_pass(db_session, slack, "C1", now=at(16), timing=TIMING) == 1
    assert approvals.notify_pass(db_session, slack, "C1", now=at(20), timing=TIMING) == 0

    assert len(slack.posted) == 1
    post = slack.posted[0]
    assert post["channel"] == "C1" and post["thread_ts"] == "999.000"
    assert "Still waiting" in post["text"] and "*restart_pod*" in post["text"]
    assert "expires in 224 minutes" in post["text"]
    assert _notes(db_session, action) == [f"approval-reminder:{action.id}"]


def test_the_second_message_goes_to_the_escalation_channel_with_an_at_here(
    db_session: Session,
) -> None:
    action = _action(db_session)
    slack = _Slack()

    approvals.notify_pass(db_session, slack, "C1", "C_BACKUP", now=at(16), timing=TIMING)
    approvals.notify_pass(db_session, slack, "C1", "C_BACKUP", now=at(31), timing=TIMING)
    approvals.notify_pass(db_session, slack, "C1", "C_BACKUP", now=at(60), timing=TIMING)

    assert [p["channel"] for p in slack.posted] == ["C1", "C_BACKUP"]
    assert "<!here>" in slack.posted[1]["text"] and "Nobody has answered" in slack.posted[1]["text"]
    assert _notes(db_session, action) == [
        f"approval-escalated:{action.id}", f"approval-reminder:{action.id}",
    ]


def test_without_a_second_channel_the_louder_message_uses_the_same_one(
    db_session: Session,
) -> None:
    _action(db_session)
    slack = _Slack()

    approvals.notify_pass(db_session, slack, "C1", None, now=at(31), timing=TIMING)

    assert [p["channel"] for p in slack.posted] == ["C1"]
    assert "<!here>" in slack.posted[0]["text"]


def test_expiry_says_nothing_was_done_and_takes_the_buttons_off(db_session: Session) -> None:
    action = _action(db_session)
    slack = _Slack()

    assert approvals.notify_pass(db_session, slack, "C1", now=at(241), timing=TIMING) == 1

    assert "Expired with no answer" in slack.posted[0]["text"]
    assert "Nothing was done" in slack.posted[0]["text"]
    assert slack.updated[0]["ts"] == "999.000"
    assert all(b["type"] == "section" for b in slack.updated[0]["blocks"])  # no actions block
    assert _notes(db_session, action) == [f"approval-expired:{action.id}"]
    # ...and never again.
    assert approvals.notify_pass(db_session, slack, "C1", now=at(300), timing=TIMING) == 0


def test_a_gateway_that_was_down_sends_only_the_latest_step(db_session: Session) -> None:
    action = _action(db_session)
    slack = _Slack()

    approvals.notify_pass(db_session, slack, "C1", now=at(500), timing=TIMING)

    assert len(slack.posted) == 1 and "Expired" in slack.posted[0]["text"]
    assert _notes(db_session, action) == [f"approval-expired:{action.id}"]


def test_a_failed_buttons_edit_does_not_lose_the_expiry_message(db_session: Session) -> None:
    action = _action(db_session)
    slack = _Slack(fail_update=True)

    approvals.notify_pass(db_session, slack, "C1", now=at(241), timing=TIMING)

    assert len(slack.posted) == 1
    assert _notes(db_session, action) == [f"approval-expired:{action.id}"]


# ── when nothing should be said ─────────────────────────────────────────────────────────────

def test_a_decided_request_gets_no_reminder_and_no_expiry(db_session: Session) -> None:
    _action(db_session, decided=True)
    slack = _Slack()

    assert approvals.notify_pass(db_session, slack, "C1", now=at(500), timing=TIMING) == 0


def test_requests_that_are_not_waiting_on_a_person_are_left_alone(db_session: Session) -> None:
    _action(db_session, policy_class=PolicyClass.auto)
    _action(db_session, policy_class=PolicyClass.never)
    _action(db_session, notified=False)  # nobody was ever told, so there is nothing to remind
    slack = _Slack()

    assert approvals.notify_pass(db_session, slack, "C1", now=at(500), timing=TIMING) == 0


def test_a_slack_outage_is_retried_not_remembered(db_session: Session) -> None:
    action = _action(db_session)

    with pytest.raises(RuntimeError):
        approvals.notify_pass(db_session, _Slack(fail=True), "C1", now=at(16), timing=TIMING)
    assert _notes(db_session, action) == []

    slack = _Slack()
    assert approvals.notify_pass(db_session, slack, "C1", now=at(17), timing=TIMING) == 1


# ── the refusal is where the decision is written ────────────────────────────────────────────

def test_a_click_just_before_the_expiry_is_recorded(db_session: Session) -> None:
    action = _action(db_session)

    assert approvals.is_expired(action, at(239.9), TIMING) is False


def test_a_click_at_the_expiry_is_refused_for_approve_and_deny(
    db_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPROVAL_EXPIRE_MINUTES", "1")
    monkeypatch.setenv("APPROVAL_REMINDER_MINUTES", "0.2")
    monkeypatch.setenv("APPROVAL_ESCALATE_MINUTES", "0.5")
    action = _action(db_session)  # proposed in 2026: far older than a minute

    for verdict in (Verdict.approved, Verdict.denied):
        outcome, decision = record_decision(db_session, action.id, verdict, actor="late")
        assert outcome is DecisionOutcome.expired and decision is None

    db_session.refresh(action)
    assert action.decision is None  # nothing was written


def test_a_fresh_request_is_still_decidable(db_session: Session) -> None:
    action = _action(db_session)
    action.created_at = datetime.now(UTC)
    db_session.flush()

    outcome, decision = record_decision(db_session, action.id, Verdict.approved, actor="roshan")

    assert outcome is DecisionOutcome.recorded and decision is not None


def test_the_refusal_needs_no_notifier_pass_to_have_run(db_session: Session) -> None:
    """The gateway's loop may be down or Slack unconfigured (staging): expiry is computed from
    the action's own age, not from anything the notifier wrote."""
    action = _action(db_session, notified=False)

    assert _notes(db_session, action) == []
    outcome, _ = record_decision(db_session, action.id, Verdict.approved, actor="late")
    assert outcome is DecisionOutcome.expired


def test_a_never_class_action_is_still_refused_as_never_not_as_expired(
    db_session: Session,
) -> None:
    action = _action(db_session, policy_class=PolicyClass.never)

    outcome, _ = record_decision(db_session, action.id, Verdict.approved, actor="x")

    assert outcome is DecisionOutcome.never_class


# ── the settings ────────────────────────────────────────────────────────────────────────────

def test_default_timing_is_15_30_240_minutes(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("REMINDER", "ESCALATE", "EXPIRE"):
        monkeypatch.delenv(f"APPROVAL_{name}_MINUTES", raising=False)

    assert approvals.Timing.from_env() == TIMING


def test_timing_can_be_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPROVAL_REMINDER_MINUTES", "1")
    monkeypatch.setenv("APPROVAL_ESCALATE_MINUTES", "2")
    monkeypatch.setenv("APPROVAL_EXPIRE_MINUTES", "3")

    assert approvals.Timing.from_env() == approvals.Timing(
        timedelta(minutes=1), timedelta(minutes=2), timedelta(minutes=3),
    )


@pytest.mark.parametrize("reminder,escalate,expire", [
    ("soon", "30", "240"),       # not a number
    ("0", "30", "240"),          # zero would remind before anything was asked
    ("-5", "30", "240"),         # negative
    ("45", "30", "240"),         # reminder after the escalation
    ("15", "30", "20"),          # expires before it escalates
    ("15", "30", "nan"),         # not a real number
    ("15", "30", "inf"),         # never expires: what this feature exists to prevent
])
def test_unusable_settings_fall_back_to_all_three_defaults(
    monkeypatch: pytest.MonkeyPatch, reminder: str, escalate: str, expire: str,
) -> None:
    monkeypatch.setenv("APPROVAL_REMINDER_MINUTES", reminder)
    monkeypatch.setenv("APPROVAL_ESCALATE_MINUTES", escalate)
    monkeypatch.setenv("APPROVAL_EXPIRE_MINUTES", expire)

    assert approvals.Timing.from_env() == TIMING
