"""The removal planner (KAV-95, ADR-0063): when a proposal to remove waste should exist, that the
model is never involved, and that nothing is asked twice, too early, or about a stale fact.

The plan is a pure function, so the rules are tested without a database; the pass that reads
incidents and writes rows uses real Postgres (those tests skip, not fail, when none is
reachable)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from kaval_agent import removal as r
from kaval_shared import health
from kaval_shared.models import (
    Action,
    BlastRadius,
    Decision,
    Execution,
    ExecutionStatus,
    Incident,
    PolicyClass,
    Proposal,
    Severity,
    Signal,
    Verdict,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
VID = "vol-0abc1234def56789a"
SID = "snap-0abc1234def56789a"
GRACE = 168.0
ON = r.Settings(enabled=True, grace_hours=1.0)


def stamp(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def volume(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "volume_id": VID,
        "state": "available",
        "size_gib": 20,
        "volume_type": "gp3",
        "days_unattached": 3,
        "monthly_cost_usd": "1.82",
    }
    return {**base, **kw}


# ── the plan, without a database ────────────────────────────────────────────────────────────────


def test_a_loose_disk_nobody_has_marked_gets_the_backup_step_first() -> None:
    p = r.plan_volume(volume(), NOW, GRACE)
    assert p is not None and p.type == r.STEP_BACKUP and p.target == VID
    assert p.reversible is True and "nothing is deleted" in p.summary
    assert "NOT deleted" in p.root_cause
    assert any("Nothing is deleted" in line for line in p.preview)  # the dry-run preview
    assert p.undo


def test_a_marked_disk_inside_the_grace_period_is_left_alone() -> None:
    v = volume(pending_removal_since=stamp(100), backup_state="completed", backup_snapshot_id=SID)
    assert r.plan_volume(v, NOW, GRACE) is None


def test_a_marked_disk_past_the_grace_with_a_finished_backup_gets_the_delete_step() -> None:
    v = volume(pending_removal_since=stamp(200), backup_state="completed", backup_snapshot_id=SID)
    p = r.plan_volume(v, NOW, GRACE)
    assert p is not None and p.type == r.STEP_DELETE and p.reversible is False
    assert SID in p.summary and SID in p.undo  # the person sees the way back
    assert any(SID in line for line in p.preview)


@pytest.mark.parametrize("state", ["pending", "error", "unknown", None])
def test_no_delete_is_asked_until_the_backup_is_finished(state: str | None) -> None:
    extra: dict[str, Any] = {"pending_removal_since": stamp(500), "backup_snapshot_id": SID}
    if state:
        extra["backup_state"] = state
    assert r.plan_volume(volume(**extra), NOW, GRACE) is None


def test_a_marked_disk_with_no_backup_at_all_is_never_asked() -> None:
    assert r.plan_volume(volume(pending_removal_since=stamp(500)), NOW, GRACE) is None


def test_a_garbled_mark_is_treated_as_unmarked_not_as_ready() -> None:
    p = r.plan_volume(
        volume(pending_removal_since="whenever", backup_state="completed"), NOW, GRACE
    )
    assert p is not None and p.type == r.STEP_BACKUP


@pytest.mark.parametrize("bad", ["", "i-0abc1234", "snap-0abc1234", "../etc"])
def test_a_target_that_is_not_a_disk_is_never_planned(bad: str) -> None:
    assert r.plan_volume(volume(volume_id=bad), NOW, GRACE) is None


def test_the_staging_grace_is_honoured() -> None:
    v = volume(pending_removal_since=stamp(2), backup_state="completed", backup_snapshot_id=SID)
    assert r.plan_volume(v, NOW, 1.0) is not None and r.plan_volume(v, NOW, GRACE) is None


def test_an_old_backup_copy_gets_one_delete_step() -> None:
    v = {"snapshot_id": SID, "class": "manual", "age_days": 120, "limit_days": 90, "size_gib": 20}
    p = r.plan_snapshot(v)
    assert p is not None and p.type == r.STEP_SNAPSHOT and p.target == SID
    assert p.reversible is False and "120 days old" in p.summary


@pytest.mark.parametrize("bad", ["", VID, "snap", "x"])
def test_a_target_that_is_not_a_copy_is_never_planned(bad: str) -> None:
    assert r.plan_snapshot({"snapshot_id": bad}) is None


def test_the_three_step_names_are_ones_the_policy_never_blocks_by_keyword() -> None:
    """The policy refuses any action type that contains pvc / namespace / iam / ec2 / billing."""
    for step in r.REMOVAL_TYPES:
        for word in ("pvc", "persistentvolumeclaim", "namespace", "iam", "ec2", "billing"):
            assert word not in step.lower(), (step, word)


def test_settings_are_off_unless_exactly_true_and_a_bad_grace_falls_back_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for value in ("", "yes", "1", "TRUE ", "false"):
        monkeypatch.setenv("KAVAL_REMOVAL_ENABLED", value)
        assert not r.Settings.from_env().enabled or value.strip().lower() == "true"
    monkeypatch.setenv("KAVAL_REMOVAL_ENABLED", "true")
    monkeypatch.setenv("KAVAL_REMOVAL_GRACE_HOURS", "banana")
    assert r.Settings.from_env() == r.Settings(True, 168.0)
    monkeypatch.setenv("KAVAL_REMOVAL_GRACE_HOURS", "0")
    assert r.Settings.from_env().grace_hours == 168.0  # a grace of nothing is not a grace period
    monkeypatch.setenv("KAVAL_REMOVAL_GRACE_HOURS", "1")
    assert r.Settings.from_env().grace_hours == 1.0
    monkeypatch.delenv("KAVAL_REMOVAL_ENABLED")
    assert not r.Settings.from_env().enabled


# ── the pass, against a real database ───────────────────────────────────────────────────────────


def _incident(
    db: Session,
    cause: str,
    target: str,
    kind: str,
    value: dict[str, Any],
    *,
    seen_hours_ago: float = 1,
    closed: bool = False,
) -> Incident:
    incident = Incident(
        id=uuid.uuid4(),
        fingerprint=f"{cause}:aws:{target}",
        severity=Severity.low,
        opened_at=NOW - timedelta(hours=30),
        closed_at=NOW if closed else None,
    )
    sig = Signal(
        source="aws_ec2",
        kind=kind,
        target=target,
        value=value,
        observed_at=NOW - timedelta(hours=seen_hours_ago),
    )
    incident.signals.append(sig)
    db.add_all([incident, sig])
    db.commit()
    return incident


def _loose(db: Session, **kw: Any) -> Incident:
    return _incident(db, "idle_volume", f"ebs/{VID}", "volume_unattached", volume(**kw))


def _restate(db: Session, incident: Incident, value: dict[str, Any], hours_after: float) -> None:
    """The next day's scan says it again: a newer signal on the same incident."""
    sig = Signal(
        source="aws_ec2",
        kind="volume_unattached",
        target=f"ebs/{VID}",
        value=value,
        observed_at=NOW + timedelta(hours=hours_after - 1),
    )
    incident.signals.append(sig)
    db.add(sig)
    db.commit()


def _count(db: Session) -> int:
    return int(db.scalar(select(func.count()).select_from(Proposal)) or 0)


def test_switched_off_it_files_nothing_and_touches_nothing(db_session: Session) -> None:
    _loose(db_session)
    assert r.plan_pass(db_session, r.Settings(False, 1.0), now=NOW) == []
    assert _count(db_session) == 0


def test_a_loose_disk_gets_one_rule_made_proposal_that_needs_a_person(db_session: Session) -> None:
    incident = _loose(db_session)
    lines = r.plan_pass(db_session, ON, now=NOW)
    assert len(lines) == 1 and VID in lines[0]
    proposal = db_session.scalars(select(Proposal)).one()
    assert proposal.incident_id == incident.id and proposal.model == r.RULE_MODEL
    assert (proposal.tokens_in, proposal.tokens_out, float(proposal.cost_usd)) == (0, 0, 0.0)
    action = db_session.scalars(select(Action)).one()
    assert action.type == r.STEP_BACKUP and action.target == VID
    assert action.blast_radius is BlastRadius.resource
    assert action.policy_class is PolicyClass.ask  # never automatic, with or without opa
    assert action.params["preview"] and action.params["undo"]


def test_a_question_still_waiting_for_an_answer_is_never_asked_a_second_time(
    db_session: Session,
) -> None:
    incident = _loose(db_session)
    r.plan_pass(db_session, ON, now=NOW)
    for day in (1, 2, 5):
        _restate(db_session, incident, volume(), 24 * day)  # each day's scan still sees it loose
        assert r.plan_pass(db_session, ON, now=NOW + timedelta(days=day)) == []
    assert _count(db_session) == 1


def _approve_and_hand_over(db: Session, action: Action, at: datetime) -> None:
    db.add(Decision(action_id=action.id, verdict=Verdict.approved, actor="roshan", decided_at=at))
    db.add(Execution(action_id=action.id, status=ExecutionStatus.success, started_at=at))
    db.commit()


def test_a_step_handed_over_is_left_to_the_worker_then_retried_only_if_nothing_came_of_it(
    db_session: Session,
) -> None:
    incident = _loose(db_session)
    r.plan_pass(db_session, ON, now=NOW)
    _approve_and_hand_over(db_session, db_session.scalars(select(Action)).one(), NOW)
    _restate(db_session, incident, volume(), 24)
    assert r.plan_pass(db_session, ON, now=NOW + timedelta(hours=24)) == []  # still its time
    _restate(db_session, incident, volume(), 49)
    assert len(r.plan_pass(db_session, ON, now=NOW + timedelta(hours=49))) == 1  # refused: retry
    assert _count(db_session) == 2


def test_a_step_the_worker_already_did_is_never_asked_again(db_session: Session) -> None:
    incident = _loose(db_session)
    r.plan_pass(db_session, ON, now=NOW)
    _approve_and_hand_over(db_session, db_session.scalars(select(Action)).one(), NOW)
    done = Signal(
        source="aws_ec2",
        kind="removal_done",
        target=f"ebs/{VID}",
        value={"order_id": "x", "step": "mark_backup", "status": "ok"},
        observed_at=NOW + timedelta(hours=1),
    )
    db_session.add(done)
    db_session.commit()
    for day in (3, 10):
        _restate(db_session, incident, volume(), 24 * day)
        assert r.plan_pass(db_session, ON, now=NOW + timedelta(days=day)) == []
    assert _count(db_session) == 1


def test_a_deleted_disk_is_never_proposed_for_deletion_again(db_session: Session) -> None:
    """Found by the end-to-end test: the scan's last fact is still fresh a day after the delete."""
    v = volume(pending_removal_since=stamp(30), backup_state="completed", backup_snapshot_id=SID)
    incident = _loose(
        db_session,
        **{k: v[k] for k in ("pending_removal_since", "backup_state", "backup_snapshot_id")},
    )
    r.plan_pass(db_session, ON, now=NOW)
    assert db_session.scalars(select(Action.type)).one() == r.STEP_DELETE
    _approve_and_hand_over(db_session, db_session.scalars(select(Action)).one(), NOW)
    db_session.add(
        Signal(
            source="aws_ec2",
            kind="removal_done",
            target=f"ebs/{VID}",
            value={"order_id": "y", "step": "delete_volume", "status": "ok"},
            observed_at=NOW + timedelta(hours=1),
        )
    )
    db_session.commit()
    _restate(db_session, incident, v, 25)
    assert r.plan_pass(db_session, ON, now=NOW + timedelta(hours=26)) == []
    assert _count(db_session) == 1


def test_a_no_is_not_asked_again_for_a_week(db_session: Session) -> None:
    incident = _loose(db_session)
    r.plan_pass(db_session, ON, now=NOW)
    action = db_session.scalars(select(Action)).one()
    db_session.add(
        Decision(action_id=action.id, verdict=Verdict.denied, actor="roshan", decided_at=NOW)
    )
    db_session.commit()
    _restate(db_session, incident, volume(), 72)
    assert r.plan_pass(db_session, ON, now=NOW + timedelta(days=3)) == []
    _restate(db_session, incident, volume(), 24 * 8)
    assert len(r.plan_pass(db_session, ON, now=NOW + timedelta(days=8))) == 1


def test_a_stale_scan_fact_is_not_acted_on(db_session: Session) -> None:
    _loose(db_session)
    assert r.plan_pass(db_session, ON, now=NOW + timedelta(hours=40)) == []


def test_a_closed_incident_is_not_acted_on(db_session: Session) -> None:
    _loose(
        db_session,
    ) if False else _incident(
        db_session, "idle_volume", f"ebs/{VID}", "volume_unattached", volume(), closed=True
    )
    assert r.plan_pass(db_session, ON, now=NOW) == []


@pytest.mark.parametrize(
    ("cause", "kind"),
    [
        ("idle_address", "address_unattached"),
        ("server_overstay", "server_overstay"),
        ("resource_unlabelled", "resource_unlabelled"),
        ("removal_failed", "removal_failed"),
        ("oom_killed", "pod_oom_killed"),
        ("kaval_loop_stalled", "kaval_loop_stalled"),
    ],
)
def test_only_the_two_removable_causes_are_ever_planned(
    db_session: Session, cause: str, kind: str
) -> None:
    _incident(
        db_session,
        cause,
        "ebs/vol-0abc1234def56789a",
        kind,
        volume(snapshot_id=SID, age_days=200, limit_days=90),
    )
    assert r.plan_pass(db_session, ON, now=NOW) == []
    assert _count(db_session) == 0


def test_an_old_backup_copy_gets_its_delete_proposal(db_session: Session) -> None:
    _incident(
        db_session,
        "snapshot_past_retention",
        f"snapshot/{SID}",
        "snapshot_past_retention",
        {"snapshot_id": SID, "class": "manual", "age_days": 120, "limit_days": 90, "size_gib": 20},
    )
    r.plan_pass(db_session, ON, now=NOW)
    assert db_session.scalars(select(Action.type)).one() == r.STEP_SNAPSHOT


def test_a_marked_disk_past_the_grace_is_asked_for_the_delete_step(db_session: Session) -> None:
    _loose(
        db_session, pending_removal_since=stamp(5), backup_state="completed", backup_snapshot_id=SID
    )
    r.plan_pass(db_session, ON, now=NOW)
    assert db_session.scalars(select(Action.type)).one() == r.STEP_DELETE


def test_no_more_than_five_removals_wait_for_an_answer_at_once(db_session: Session) -> None:
    for i in range(8):
        vid = f"vol-0abc1234def5678{i}"
        _incident(
            db_session, "idle_volume", f"ebs/{vid}", "volume_unattached", volume(volume_id=vid)
        )
    for _ in range(4):
        r.plan_pass(db_session, ON, now=NOW)
    assert _count(db_session) == r.MAX_OPEN


def test_the_model_queue_skips_exactly_these_causes_only_when_removal_is_on(
    db_session: Session,
) -> None:
    from kaval_agent import diagnoser as d

    _loose(db_session)
    assert len(d.waiting_incidents(db_session, NOW, timedelta(days=2))) == 1  # off: the old path
    assert d.waiting_incidents(db_session, NOW, timedelta(days=2), skip_removable=True) == []


def test_removal_proposals_are_the_only_ones_with_the_rule_marker() -> None:
    assert r.RULE_MODEL == "rule:removal"
    assert health.REMOVABLE_CAUSES == {"idle_volume", "snapshot_past_retention"}
