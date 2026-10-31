"""Only one gateway copy notifies at a time (KAV-100, ADR-0069), against a real Postgres.

Found live in production on 2026-10-27: two gateway copies each asked "was this reminder sent?" and
then posted, with nothing between the two, so the "nobody has answered" alert arrived twice. These
tests commit real rows (the shared `db_session` rolls back, which cannot show two connections
racing) and clean them up afterwards. Slack is a fake that is slow on purpose, so both copies are
inside the window at once."""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from kaval_gateway import approvals, slack_chatops
from kaval_gateway.notifier_lock import only_one_notifier
from kaval_shared.models import (
    Action,
    BlastRadius,
    Incident,
    Notification,
    PolicyClass,
    Proposal,
    RiskLevel,
    Severity,
    Signal,
)
from sqlalchemy import Engine, delete, select, text
from sqlalchemy.orm import Session

# Older than any expiry, so the step that is due is "expired".
LONG_AGO = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


class _SlowSlack:
    """Records what was posted, but takes a moment to do it, like the real thing."""

    def __init__(self, delay: float = 0.3) -> None:
        self.posted: list[dict[str, Any]] = []
        self.delay = delay
        self._lock = threading.Lock()

    def chat_postMessage(self, **kwargs: Any) -> dict[str, Any]:  # noqa: N802
        time.sleep(self.delay)
        with self._lock:
            self.posted.append(kwargs)
        return {"ok": True, "ts": "111.222"}

    def chat_update(self, **kwargs: Any) -> dict[str, Any]:
        return {"ok": True}


@pytest.fixture
def waiting_action(migrated_engine: Engine) -> Iterator[str]:
    """One committed, long-unanswered `ask` request. Yields its target; removes it afterwards."""
    target = f"test-lock/{uuid.uuid4().hex[:8]}"
    with Session(migrated_engine) as db:
        signal = Signal(source="kubernetes", kind="pod_back_off", target=target, value={},
                        observed_at=LONG_AGO)
        incident = Incident(fingerprint=f"test-{uuid.uuid4()}", severity=Severity.high,
                            opened_at=LONG_AGO, signals=[signal])
        proposal = Proposal(incident=incident, summary="restart it", root_cause="oom",
                            confidence=0.8, risk=RiskLevel.low, model="m", tokens_in=1,
                            tokens_out=1, cost_usd=Decimal("0"), created_at=LONG_AGO)
        action = Action(proposal=proposal, type="restart_pod", target=target, params={},
                        reversible=True, blast_radius=BlastRadius.pod,
                        policy_class=PolicyClass.ask, created_at=LONG_AGO,
                        slack_notified_at=LONG_AGO, slack_ts="999.000")
        db.add_all([incident, proposal, action])
        db.flush()
        action_id, proposal_id, incident_id = action.id, proposal.id, incident.id
        db.commit()
    try:
        yield target
    finally:
        with Session(migrated_engine) as db:
            db.execute(delete(Notification).where(Notification.key.like(f"approval-%:{action_id}")))
            db.execute(delete(Action).where(Action.id == action_id))
            db.execute(delete(Proposal).where(Proposal.id == proposal_id))
            db.execute(text("DELETE FROM incident_signal WHERE incident_id = :i"),
                       {"i": incident_id})
            db.execute(delete(Incident).where(Incident.id == incident_id))
            db.execute(delete(Signal).where(Signal.target == target))
            db.commit()


def _about(slack: _SlowSlack, target: str) -> list[dict[str, Any]]:
    return [p for p in slack.posted if target in p.get("text", "")]


def _two_copies(run: Any) -> None:
    """Run the same callable in two threads that start together, like two gateway copies."""
    start = threading.Barrier(2)

    def go() -> None:
        start.wait()
        run()

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)


# ── the lock itself ─────────────────────────────────────────────────────────────────────────

def test_the_first_copy_gets_the_lock_and_a_second_copy_does_not(migrated_engine: Engine) -> None:
    with only_one_notifier(migrated_engine) as first:
        assert first is True
        with only_one_notifier(migrated_engine) as second:
            assert second is False


def test_the_lock_is_free_again_after_a_pass(migrated_engine: Engine) -> None:
    with only_one_notifier(migrated_engine):
        pass
    with only_one_notifier(migrated_engine) as again:
        assert again is True


def test_the_lock_is_released_even_when_the_pass_fails(migrated_engine: Engine) -> None:
    with pytest.raises(RuntimeError), only_one_notifier(migrated_engine):
        raise RuntimeError("slack fell over mid-pass")
    with only_one_notifier(migrated_engine) as again:
        assert again is True


# ── the bug, and the fix ────────────────────────────────────────────────────────────────────

def test_without_the_lock_two_copies_send_the_same_message_twice(
    migrated_engine: Engine, waiting_action: str,
) -> None:
    """The bug as found live. If this ever starts passing with one message, the race has been
    closed some other way and the lock can be reconsidered; until then it proves the next test
    would catch a regression."""
    slack = _SlowSlack()

    def old_loop() -> None:
        with Session(migrated_engine) as session:
            approvals.notify_pass(session, slack, "C1")

    _two_copies(old_loop)

    assert len(_about(slack, waiting_action)) == 2


def test_with_the_lock_two_copies_send_it_once(
    migrated_engine: Engine, waiting_action: str,
) -> None:
    slack = _SlowSlack()
    ran: list[bool] = []

    _two_copies(lambda: ran.append(slack_chatops.notify_round(migrated_engine, slack, "C1")))

    assert len(_about(slack, waiting_action)) == 1
    assert sorted(ran) == [False, True]  # one copy did the round, the other stood aside
    with Session(migrated_engine) as db:
        keys = list(db.scalars(select(Notification.key).where(
            Notification.key.like("approval-expired:%"),
            Notification.message.like(f"%{waiting_action}%"),
        )))
    assert len(keys) == 1


def test_a_copy_that_stood_aside_sends_nothing_and_a_later_round_still_works(
    migrated_engine: Engine, waiting_action: str,
) -> None:
    slack = _SlowSlack(delay=0)
    with only_one_notifier(migrated_engine) as holder:
        assert holder is True
        assert slack_chatops.notify_round(migrated_engine, slack, "C1") is False
        assert _about(slack, waiting_action) == []

    assert slack_chatops.notify_round(migrated_engine, slack, "C1") is True
    assert len(_about(slack, waiting_action)) == 1
