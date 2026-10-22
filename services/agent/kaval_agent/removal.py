"""The removal planner: files the proposals that remove waste, by code and never by the model
(KAV-95, ADR-0063).

The waste scan finds a disk attached to nothing and a backup copy past its retention. Kaval can
remove both, but only the careful way, and only with a person's approval at every step:

  disk, step 1   `backup_and_mark_volume`   label it, take a backup copy; nothing is deleted
  (wait)         the grace period (7 days in production, 1 hour in staging)
  disk, step 2   `delete_idle_volume`       delete it, once the backup is finished
  backup copy    `delete_old_snapshot`      delete a copy that is past its retention

This module decides *whether a proposal should exist* from facts the scan reads off AWS's own
labels (when the disk was marked, whether its backup finished), and writes it. It never asks the
model: a model that is confused or manipulated has no say in what gets deleted. The proposal's
`model` is the fixed word `rule:removal`, which is also what the executor requires before it will
carry out a removal (`kaval_executor.executor`), so a proposal from any other route is refused.

Nothing here has an AWS or Kubernetes login. It only writes `proposal` and `action` rows. The
policy classifies every action `ask` (nothing is ever automatic), a person approves it in the app,
the executor writes an order, and the removal worker (infra/modules/waste-removal) re-checks AWS
itself before touching anything.

The plan is a pure function (`plan_volume`, `plan_snapshot`) so every rule is tested without a
database; `plan_pass` is the part that reads incidents and writes rows.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from kaval_shared import health
from kaval_shared.models import (
    Action,
    BlastRadius,
    Decision,
    Execution,
    Incident,
    Proposal,
    RiskLevel,
    Signal,
    Verdict,
)
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from kaval_agent import policy

LOCK_KEY = 0x4B41_5652  # "KAVR"; not the correlator's or the diagnoser's
RULE_MODEL = health.REMOVAL_MODEL

STEP_BACKUP = "backup_and_mark_volume"
STEP_DELETE = "delete_idle_volume"
STEP_SNAPSHOT = "delete_old_snapshot"
REMOVAL_TYPES = frozenset({STEP_BACKUP, STEP_DELETE, STEP_SNAPSHOT})

# A scan fact older than this is stale: the scan runs daily and the server copies it every few
# hours.
FACT_MAX_AGE = timedelta(hours=36)
# A step that was handed over and came back refused or failed may be tried again after this pause.
# Step one's label only shows in the next scan (up to a day plus the copy), so it must outlast that.
COOLDOWN = {STEP_BACKUP: timedelta(hours=48), STEP_DELETE: timedelta(hours=24),
            STEP_SNAPSHOT: timedelta(hours=24)}
DENIED_COOLDOWN = timedelta(days=7)  # a "no" is not asked again for a week
MAX_OPEN = 5  # at most this many removal proposals waiting for an answer
# An unanswered proposal is dead once its approval has expired (4 hours in production), but expiry
# is worked out from the age and never written down, so the planner cannot see it. It treats a
# question older than this as dead: it may ask again, and the dead one no longer counts toward
# MAX_OPEN. Must stay at or above every environment's approval expiry (a test reads the values
# files and checks).
ASK_AGAIN_AFTER = timedelta(hours=24)
MAX_PER_PASS = 3
DEFAULT_GRACE_HOURS = 168.0


@dataclass(frozen=True)
class Settings:
    enabled: bool
    grace_hours: float

    @classmethod
    def from_env(cls) -> Settings:
        """`KAVAL_REMOVAL_ENABLED` (off unless exactly "true") and `KAVAL_REMOVAL_GRACE_HOURS`
        (must equal the worker's, which re-checks it; a bad value falls back to the safe 7 days)."""
        try:
            grace = float(os.environ.get("KAVAL_REMOVAL_GRACE_HOURS", DEFAULT_GRACE_HOURS))
        except ValueError:
            grace = DEFAULT_GRACE_HOURS
        return cls(os.environ.get("KAVAL_REMOVAL_ENABLED", "").lower() == "true",
                   grace if grace >= 1 else DEFAULT_GRACE_HOURS)


@dataclass(frozen=True)
class Planned:
    """One proposal that should exist: what to show a person and the one action behind it."""

    type: str
    target: str  # the AWS id, e.g. vol-0abc...
    summary: str
    root_cause: str
    reversible: bool
    risk: RiskLevel
    preview: tuple[str, ...]
    undo: str
    # What the proposal promises to save each month, as the scan's own text ("1.82"); empty when
    # nobody put a price on it (a backup copy's saving depends on what it shares with others).
    # The savings report (KAV-97) reads this number, never the wording.
    saving_usd_month: str = ""

    def params(self) -> dict[str, object]:
        out: dict[str, object] = {"preview": list(self.preview), "undo": self.undo}
        if self.saving_usd_month:
            out["saving_usd_month"] = self.saving_usd_month
        return out


def _stamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


_MONEY = re.compile(r"\d{1,6}\.\d{2}")


def _promised(value: Mapping[str, Any]) -> str:
    """The scan's monthly cost as a plain amount, or "" if it is missing or not in that shape."""
    cost = value.get("monthly_cost_usd")
    return cost if isinstance(cost, str) and _MONEY.fullmatch(cost) else ""


def _money(value: Mapping[str, Any]) -> str:
    cost = value.get("monthly_cost_usd")
    return f"${cost} a month" if isinstance(cost, str) else "a few cents a month"


def plan_volume(value: Mapping[str, Any], now: datetime, grace_hours: float) -> Planned | None:
    """The next step for a disk attached to nothing, or None when there is nothing to ask yet
    (the grace period is running, or the backup is not finished, or it failed)."""
    vid, size = str(value.get("volume_id", "")), value.get("size_gib")
    if not vid.startswith("vol-"):
        return None
    marked = _stamp(value.get("pending_removal_since"))
    how_big = f"{size} GiB, " if isinstance(size, int) else ""
    if marked is None:
        return Planned(
            type=STEP_BACKUP,
            target=vid,
            summary=(
                f"Back up and mark the loose disk {vid} ({how_big}{_money(value)}) "
                "for removal; nothing is deleted yet."
            ),
            root_cause=(
                f"Disk {vid} has been attached to nothing for {value.get('days_unattached', '?')} "
                "day(s) and costs money for nothing. This is step 1 of 2: it takes a backup "
                f"copy and labels the disk. The disk is NOT deleted. After {grace_hours:g} hour(s) "
                "you will be asked again before anything is deleted."
            ),
            reversible=True,
            risk=RiskLevel.low,
            preview=(
                f"Create a backup copy of {vid} (labelled as Kaval's, kept 30 days).",
                f"Label {vid} with KavalRemoval=<now>.",
                "Nothing is deleted. If anyone attaches the disk meanwhile, the removal cancels.",
                f"Step 2 (the delete) is asked again after {grace_hours:g} hour(s).",
            ),
            undo="Remove the KavalRemoval label; the backup copy can simply be deleted.",
        )
    if value.get("backup_state") != "completed":
        return None  # the backup is not finished (or failed): a person looks, nothing is asked
    if now - marked < timedelta(hours=grace_hours):
        return None
    backup = str(value.get("backup_snapshot_id", "the backup copy"))
    return Planned(
        type=STEP_DELETE,
        target=vid,
        summary=(
            f"Delete the loose disk {vid} ({how_big}{_money(value)}). Backed up as {backup}."
        ),
        root_cause=(
            f"Disk {vid} was marked for removal at {value.get('pending_removal_since')}, has "
            f"stayed attached to nothing for more than {grace_hours:g} hour(s), and its backup "
            f"{backup} is finished. This is step 2 of 2: it deletes the disk. The worker checks "
            "again before deleting and refuses if the disk was attached in the meantime."
        ),
        reversible=False,
        risk=RiskLevel.medium,
        preview=(
            f"Delete the disk {vid} (marked {value.get('pending_removal_since')}).",
            f"The backup copy {backup} is kept for 30 days.",
            f"Saves {_money(value)}.",
        ),
        undo=f"Create a new disk from the backup copy {backup} (a restore).",
        saving_usd_month=_promised(value),
    )


def plan_snapshot(value: Mapping[str, Any]) -> Planned | None:
    """A backup copy past its retention: one step, a delete, after approval."""
    sid = str(value.get("snapshot_id", ""))
    if not sid.startswith("snap-"):
        return None
    age, limit, size = value.get("age_days"), value.get("limit_days"), value.get("size_gib")
    old = f"{age} days old (limit {limit})" if isinstance(age, int) else "past its retention"
    return Planned(
        type=STEP_SNAPSHOT,
        target=sid,
        summary=f"Delete the old backup copy {sid} ({size} GiB, {old}).",
        root_cause=(
            f"Backup copy {sid} is {old} and is not the newest copy of any disk that still "
            "exists. Deleting it frees its storage. This cannot be undone, but a copy past its "
            "retention is by definition no longer needed."
        ),
        reversible=False,
        risk=RiskLevel.medium,
        preview=(
            f"Delete the backup copy {sid}.",
            f"It is {old}; the worker checks again that it is not the newest copy of a live disk.",
        ),
        undo="None: the copy is gone. It was past its retention.",
    )


# ── the database half ───────────────────────────────────────────────────────────────────────────


def _latest(incident: Incident, kind: str) -> Signal | None:
    rows = [s for s in incident.signals if s.kind == kind]
    return max(rows, key=lambda s: s.observed_at, default=None)


def _already_done(session: Session, planned: Planned) -> bool:
    """True if the worker already answered `ok` for this step on this thing. A disk's or a copy's id
    is never reused, so a step that worked is never asked again, whatever the scan still says."""
    prefix = "snapshot/" if planned.type == STEP_SNAPSHOT else "ebs/"
    step = {STEP_BACKUP: "mark_backup", STEP_DELETE: "delete_volume",
            STEP_SNAPSHOT: "delete_snapshot"}[planned.type]
    return (
        session.scalar(
            select(Signal.id)
            .where(
                Signal.kind == "removal_done",
                Signal.target == prefix + planned.target,
                Signal.value.contains({"step": step}),
            )
            .limit(1)
        )
        is not None
    )


def _skip(session: Session, planned: Planned, now: datetime) -> str | None:
    """Why this step should not be asked now, or None when it should be."""
    if _already_done(session, planned):
        return "already done"
    latest = session.execute(
        select(Action, Decision.verdict, Execution.id)
        .outerjoin(Decision, Decision.action_id == Action.id)
        .outerjoin(Execution, Execution.action_id == Action.id)
        .where(Action.type == planned.type, Action.target == planned.target)
        .order_by(Action.created_at.desc())
        .limit(1)
    ).first()
    if latest is None:
        return None
    action, verdict, execution = latest
    if verdict is None:
        # never a second copy of a live question; an expired one may be asked again
        if now - action.created_at < ASK_AGAIN_AFTER:
            return "already waiting for an answer"
        return None
    if verdict == Verdict.denied:
        return "denied recently" if now - action.created_at < DENIED_COOLDOWN else None
    # approved: a step handed over and not yet answered is left to the worker (and to the alarm for
    # an order nobody answered); a refused one may be tried again after a pause
    if execution is not None and now - action.created_at < COOLDOWN[planned.type]:
        return "handed over recently"
    return None


def _open_removals(session: Session, now: datetime) -> int:
    """Removal proposals nobody has answered yet and whose approval has not run out."""
    return int(
        session.scalar(
            select(func.count())
            .select_from(Action)
            .outerjoin(Decision, Decision.action_id == Action.id)
            .where(
                Action.type.in_(REMOVAL_TYPES),
                Decision.id.is_(None),
                Action.created_at > now - ASK_AGAIN_AFTER,
            )
        )
        or 0
    )


def file_proposal(
    session: Session, incident: Incident, planned: Planned, now: datetime | None = None
) -> Proposal:
    """One `proposal` row and its one `action` row. The policy classifies the action from the
    incident's cause; with no `opa` it says `ask`, never `auto`.

    The action is stamped with the planner's own clock, not the database's: `_skip` measures its
    pauses ("not again for 48 hours") from this time, and a test that moves the clock by hand
    passed only on the day it was written when the two clocks were mixed (found 2026-10-21)."""
    cause = health.cause_of_fingerprint(incident.fingerprint)
    proposal = Proposal(
        incident_id=incident.id,
        summary=planned.summary[:280],
        root_cause=planned.root_cause,
        confidence=1.0,  # a rule, not a guess; the policy still says ask
        risk=planned.risk,
        model=RULE_MODEL,
        tokens_in=0,
        tokens_out=0,
        cost_usd=Decimal("0"),
    )
    session.add(proposal)
    session.flush()
    session.add(
        Action(
            proposal_id=proposal.id,
            type=planned.type,
            target=planned.target,
            params=planned.params(),
            reversible=planned.reversible,
            blast_radius=BlastRadius.resource,
            policy_class=policy.classify(
                planned.type, BlastRadius.resource, planned.reversible, 1.0, cause=cause
            ),
            created_at=now or datetime.now(UTC),
        )
    )
    return proposal


def plan_pass(
    session: Session, settings: Settings, *, now: datetime | None = None
) -> list[str]:
    """One pass: for each open incident about a removable thing, file the next step if one is due.
    Returns the lines to print. A no-op unless removal is switched on."""
    if not settings.enabled:
        return []
    now = now or datetime.now(UTC)
    if not session.scalar(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": LOCK_KEY}):
        return ["another removal planner holds the lock; skipped this pass"]
    open_now = _open_removals(session, now)
    # When the latest complete scan ran. A sighting older than that was not repeated by it: the
    # disk (or copy) is gone, or no longer waste. None before the first record exists, then the
    # age limit alone applies.
    last_scan = session.scalar(
        select(func.max(Signal.observed_at)).where(Signal.kind == health.WASTE_SCAN_DONE)
    )
    lines: list[str] = []
    incidents = session.scalars(
        select(Incident).where(Incident.closed_at.is_(None)).order_by(Incident.opened_at)
    ).all()
    for incident in incidents:
        if len(lines) >= MAX_PER_PASS or open_now >= MAX_OPEN:
            break
        cause = health.cause_of_fingerprint(incident.fingerprint)
        if cause not in health.REMOVABLE_CAUSES:
            continue
        kind = "volume_unattached" if cause == "idle_volume" else "snapshot_past_retention"
        fact = _latest(incident, kind)
        if fact is None or now - fact.observed_at > FACT_MAX_AGE:
            continue
        if last_scan is not None and fact.observed_at < last_scan:
            continue  # the latest scan did not list it again
        planned = (
            plan_volume(fact.value, now, settings.grace_hours)
            if cause == "idle_volume"
            else plan_snapshot(fact.value)
        )
        if planned is None or _skip(session, planned, now) is not None:
            continue
        proposal = file_proposal(session, incident, planned, now)
        open_now += 1
        lines.append(f"proposed {planned.type} {planned.target} (proposal {proposal.id})")
    session.commit()
    return lines

