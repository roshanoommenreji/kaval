"""The savings report (KAV-97, ADR-0065): promised against confirmed.

What matters most is what must NOT be counted as saved: a thing nobody approved, a thing approved
but not yet confirmed gone, a failed removal, an answer to an older question, another disk's answer,
and the same thing asked about twice. Money amounts that are missing or malformed read as "no
price", never as zero and never as a guess."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from kaval_agent.removal import STEP_BACKUP, STEP_DELETE, STEP_SNAPSHOT
from kaval_executor import savings as s
from kaval_shared.models import (
    Action,
    BlastRadius,
    Decision,
    Incident,
    PolicyClass,
    Proposal,
    RiskLevel,
    Severity,
    Signal,
    Verdict,
)
from sqlalchemy.orm import Session

ASKED = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
VID = "vol-0abc1234def56789a"
VID2 = "vol-0def1234abc56789b"
SID = "snap-0abc1234def56789a"


def ask(
    db: Session,
    *,
    kind: str = STEP_DELETE,
    target: str = VID,
    price: str | None = "1.82",
    verdict: Verdict | None = Verdict.approved,
    at: datetime = ASKED,
) -> Action:
    fingerprint = f"x:{target}:{at.isoformat()}"
    incident = Incident(fingerprint=fingerprint, severity=Severity.low, opened_at=at)
    proposal = Proposal(
        incident=incident, summary="s", root_cause="r", confidence=1.0, risk=RiskLevel.low,
        model="rule:removal", tokens_in=0, tokens_out=0, cost_usd=Decimal("0"),
    )
    params: dict[str, object] = {"preview": [], "undo": "u"}
    if price is not None:
        params["saving_usd_month"] = price
    action = Action(
        proposal=proposal, type=kind, target=target, params=params, reversible=False,
        blast_radius=BlastRadius.resource, policy_class=PolicyClass.ask, created_at=at,
    )
    db.add_all([incident, proposal, action])
    db.flush()
    if verdict is not None:
        db.add(Decision(action_id=action.id, verdict=verdict, actor="test", decided_at=at))
        db.flush()
    return action


def worker_said(
    db: Session, kind: str, target: str, *, at: datetime, saving: str | None = "1.82"
) -> None:
    value: dict[str, Any] = {"order_id": "ord-1", "step": "delete_volume", "status": "ok"}
    if saving is not None:
        value["monthly_saving_usd"] = saving
    prefix = "snapshot/" if target.startswith("snap-") else "ebs/"
    db.add(Signal(source="aws_ec2", kind=kind, target=prefix + target, value=value, observed_at=at))
    db.flush()


def only(db: Session, now: datetime) -> s.Row:
    found = s.rows(db, now)
    assert len(found) == 1
    return found[0]


# ── what counts as saved ────────────────────────────────────────────────────────────────────────


def test_an_approved_delete_the_worker_confirmed_is_saved(db_session: Session) -> None:
    ask(db_session)
    worker_said(db_session, "removal_done", VID, at=ASKED + timedelta(hours=1))
    now = ASKED + timedelta(days=5, hours=1)
    row = only(db_session, now)
    assert row.state == "confirmed" and row.proposed == Decimal("1.82") == row.realised
    assert row.days_saved(now) == 5
    assert row.saved_so_far(now) == Decimal("1.82") * 5 / 30


def test_approved_but_not_yet_confirmed_is_not_saved(db_session: Session) -> None:
    ask(db_session)
    now = ASKED + timedelta(hours=2)
    row = only(db_session, now)
    assert row.state == "approved" and row.realised is None
    t = s.totals([row], now)
    assert t.approved == Decimal("1.82") and t.realised == 0 and t.saved_so_far == 0


def test_a_confirmation_without_a_yes_behind_it_is_not_counted(db_session: Session) -> None:
    ask(db_session, verdict=None)
    worker_said(db_session, "removal_done", VID, at=ASKED + timedelta(hours=1))
    row = only(db_session, ASKED + timedelta(hours=2))
    assert row.state == "waiting" and row.realised is None


def test_a_removal_that_failed_is_not_saved(db_session: Session) -> None:
    ask(db_session)
    worker_said(db_session, "removal_failed", VID, at=ASKED + timedelta(hours=1))
    row = only(db_session, ASKED + timedelta(days=2))
    assert row.state == "approved" and row.realised is None


def test_a_no_is_declined_and_never_counted_as_approved(db_session: Session) -> None:
    ask(db_session, verdict=Verdict.denied)
    now = ASKED + timedelta(days=1)
    row = only(db_session, now)
    t = s.totals([row], now)
    assert row.state == "declined" and t.approved == 0 and t.realised == 0
    assert t.proposed == Decimal("1.82")  # it was on the table


def test_a_question_nobody_answered_in_a_day_is_expired(db_session: Session) -> None:
    ask(db_session, verdict=None)
    assert only(db_session, ASKED + timedelta(hours=3)).state == "waiting"
    assert only(db_session, ASKED + timedelta(hours=25)).state == "expired"


def test_an_answer_to_an_older_question_is_not_this_ones(db_session: Session) -> None:
    worker_said(db_session, "removal_done", VID, at=ASKED - timedelta(days=3))
    ask(db_session)
    assert only(db_session, ASKED + timedelta(days=1)).state == "approved"


def test_another_disks_answer_is_not_this_disks(db_session: Session) -> None:
    ask(db_session)
    worker_said(db_session, "removal_done", VID2, at=ASKED + timedelta(hours=1))
    assert only(db_session, ASKED + timedelta(days=1)).state == "approved"


def test_the_backup_step_saves_nothing_and_is_not_listed(db_session: Session) -> None:
    ask(db_session, kind=STEP_BACKUP, price=None)
    assert s.rows(db_session, ASKED + timedelta(days=1)) == []


def test_asked_twice_is_one_row_in_its_furthest_state(db_session: Session) -> None:
    ask(db_session, verdict=None, at=ASKED - timedelta(days=2))  # the first question expired
    ask(db_session)  # asked again and approved
    worker_said(db_session, "removal_done", VID, at=ASKED + timedelta(hours=1))
    now = ASKED + timedelta(days=2)
    found = s.rows(db_session, now)
    assert len(found) == 1 and found[0].state == "confirmed"
    assert s.totals(found, now).proposed == Decimal("1.82")  # counted once


# ── prices that are missing or wrong ────────────────────────────────────────────────────────────


def test_a_backup_copy_has_no_price_and_is_counted_without_an_amount(db_session: Session) -> None:
    ask(db_session, kind=STEP_SNAPSHOT, target=SID, price=None)
    worker_said(db_session, "removal_done", SID, at=ASKED + timedelta(hours=1), saving=None)
    now = ASKED + timedelta(days=3)
    row = only(db_session, now)
    t = s.totals([row], now)
    assert row.state == "confirmed" and row.proposed is None and row.realised is None
    assert t.counts["confirmed"] == 1 and t.unpriced == 1
    assert t.proposed == t.realised == t.saved_so_far == 0


@pytest.mark.parametrize("bad", ["abc", "-1.00", "NaN", "Infinity", "", None, 1.82, ["1"]])
def test_a_missing_or_malformed_amount_reads_as_no_price(bad: object) -> None:
    assert s._amount(bad) is None


def test_a_normal_amount_reads_as_a_number() -> None:
    assert s._amount("0.09") == Decimal("0.09")


# ── the printed report ──────────────────────────────────────────────────────────────────────────


def test_an_empty_report_says_so() -> None:
    assert s.render([], ASKED) == ["nothing has been proposed for deletion yet"]


def test_the_report_states_what_realised_means(db_session: Session) -> None:
    ask(db_session)
    worker_said(db_session, "removal_done", VID, at=ASKED + timedelta(hours=1))
    now = ASKED + timedelta(days=2)
    text = "\n".join(s.render(s.rows(db_session, now), now))
    assert "confirmed gone (realised)      $1.82 a month" in text
    assert "Not read off the AWS bill" in text
    assert VID in text
