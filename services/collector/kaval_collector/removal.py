"""Reads the removal worker's answers the server left in the cluster (KAV-95, ADR-0063).

The worker (infra/modules/waste-removal) is the only thing that deletes waste in AWS. When a person
approves a removal, the executor writes an order; the server's timer carries it out to AWS and
carries the worker's answer back into one ConfigMap; this reads that ConfigMap. It runs in the same
pod as the cost reader and the waste scan reader (`kaval_collector.cost_explorer`), holds no AWS
login, and follows the same rules as `waste.py`:

- **Only what it recognises is written.** The ConfigMap is a place a person or a bug could put text
  in front of a model, so every answer is rebuilt from a whitelist: fixed words, AWS ids, a date.
  Anything else is dropped and counted, never stored as it came.
- **Once per answer.** An answer stays in the ConfigMap for a while; each is written once, so a
  retried pass or a restart changes nothing.
- **A worked step is a record, a failed one is an incident for a person.** `removal_done` opens
  nothing (the correlator ignores it); `removal_failed` (the worker refused or an AWS call failed)
  becomes an incident, because an approved action that did not happen is something to know.
- **An order nobody answered is loud.** The oldest order with no answer yet is a number; a rule
  turns six hours of it into an alarm (deploy/charts/kaval/files/kaval-removal.rules.yaml).

An answer the worker never wrote is never believed: its `at` stamp, its fixed `reason` words and
the order's own id are the only things trusted, and only after they match their shapes.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from kaval_shared.models import Signal
from prometheus_client import Gauge
from sqlalchemy import select
from sqlalchemy.orm import Session

# `fullmatch`, never `match` with a `$`: a `$` also accepts a trailing newline.
SOURCE = "aws_ec2"
STEPS = ("mark_backup", "delete_volume", "delete_snapshot")
STATUSES = ("ok", "refused", "failed")
# The worker's fixed words (infra/modules/waste-removal/lambda/waste_removal.py REASONS).
REASONS = frozenset(
    {
        "ok",
        "disabled",
        "bad_order",
        "not_found",
        "not_kaval",
        "other_env",
        "terraform_owned",
        "not_labelled",
        "attached",
        "already_marked",
        "not_marked",
        "grace_not_over",
        "no_backup",
        "backup_unfinished",
        "not_past_retention",
        "newest_copy",
        "in_use_by_image",
        "aws_failed",
    }
)

_ORDER_ID = re.compile(r"[0-9a-f-]{8,40}")
_TARGET = re.compile(r"(vol|snap)-[0-9a-f]{8,17}")
_SNAPSHOT = re.compile(r"snap-[0-9a-f]{8,17}")
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_MONEY = re.compile(r"\d{1,6}\.\d{2}")

_UNANSWERED = Gauge(
    "kaval_removal_oldest_unanswered_order_seconds",
    "Age of the oldest removal order the worker has not answered yet (0 when there is none)",
)
_DROPPED = Gauge(
    "kaval_removal_answers_dropped",
    "Answers in the worker's result this program refused: not in the expected shape",
)


@dataclass(frozen=True)
class Answer:
    """One answer the worker gave, rebuilt from the whitelist."""

    order_id: str
    step: str
    target: str
    status: str
    reason: str
    at: datetime
    backup: str
    saved_usd: str

    @property
    def signal_kind(self) -> str:
        return "removal_done" if self.status == "ok" else "removal_failed"

    @property
    def signal_target(self) -> str:
        return ("ebs/" if self.target.startswith("vol-") else "snapshot/") + self.target


def _stamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def check(raw: object) -> Answer | None:
    """One answer rebuilt from the whitelist, or None if any part of it is not as expected."""
    if not isinstance(raw, Mapping):
        return None
    oid, step, target = raw.get("id"), raw.get("step"), raw.get("target")
    status, reason, at = raw.get("status"), raw.get("reason"), raw.get("at")
    if not (isinstance(oid, str) and _ORDER_ID.fullmatch(oid)):
        return None
    if step not in STEPS or status not in STATUSES:
        return None
    if not (isinstance(target, str) and _TARGET.fullmatch(target)):
        return None
    # a disk step names a disk and a copy step names a copy, never the other way round
    if (step == "delete_snapshot") != target.startswith("snap-"):
        return None
    if not (isinstance(reason, str) and reason in REASONS):
        return None
    if (status == "ok") != (reason == "ok"):
        return None
    if not (isinstance(at, str) and _STAMP.fullmatch(at)):
        return None
    backup, saved = raw.get("backup", ""), raw.get("saved_usd", "")
    if backup and not (isinstance(backup, str) and _SNAPSHOT.fullmatch(backup)):
        return None
    if saved and not (isinstance(saved, str) and _MONEY.fullmatch(saved)):
        return None
    return Answer(oid, str(step), target, str(status), reason, _stamp(at), str(backup), str(saved))


@dataclass(frozen=True)
class Handoff:
    """What the server left in the ConfigMaps. Both parts are parsed JSON, not yet trusted."""

    answers: object | None
    orders: object | None


class HandoffReader(Protocol):
    def read(self) -> Handoff: ...


def parse(data: Mapping[str, str] | None, key: str, name: str) -> object | None:
    """The list under `name` in the JSON document at `key`, or None if there is none."""
    if not data or key not in data:
        return None
    try:
        doc = json.loads(data[key])
    except ValueError:
        return None
    return doc.get(name) if isinstance(doc, dict) else None


def _already_written(session: Session, a: Answer) -> bool:
    return (
        session.scalar(
            select(Signal.id)
            .where(
                Signal.source == SOURCE,
                Signal.kind == a.signal_kind,
                Signal.target == a.signal_target,
                Signal.value.contains({"order_id": a.order_id}),
            )
            .limit(1)
        )
        is not None
    )


def ingest(session: Session, answers: Sequence[Answer]) -> list[str]:
    """Write the answers not already in the table. One commit."""
    written: list[str] = []
    for a in answers:
        if _already_written(session, a):
            continue
        value: dict[str, Any] = {
            "order_id": a.order_id,
            "step": a.step,
            "status": a.status,
            "reason": a.reason,
        }
        if a.backup:
            value["backup_snapshot_id"] = a.backup
        if a.saved_usd:
            value["monthly_saving_usd"] = a.saved_usd
        session.add(
            Signal(
                source=SOURCE,
                kind=a.signal_kind,
                target=a.signal_target,
                observed_at=a.at,
                value=value,
            )
        )
        written.append(f"{a.signal_kind:15} {a.signal_target} {a.step} {a.reason}")
    session.commit()
    return written


def oldest_unanswered(orders: object, answered: set[str], now: datetime) -> float:
    """Seconds since the oldest order without an answer was written; 0 if there is none. An order
    whose stamp cannot be read counts from now, so a garbled one never looks like a stuck one."""
    if not isinstance(orders, list):
        return 0.0
    oldest = 0.0
    for raw in orders:
        if not isinstance(raw, Mapping):
            continue
        oid, at = raw.get("id"), raw.get("at")
        if not (isinstance(oid, str) and _ORDER_ID.fullmatch(oid)) or oid in answered:
            continue
        if isinstance(at, str) and _STAMP.fullmatch(at):
            oldest = max(oldest, (now - _stamp(at)).total_seconds())
    return max(oldest, 0.0)


def start() -> None:
    _UNANSWERED.set(0)
    _DROPPED.set(0)


def pass_failed() -> None:
    """The pass itself broke: the stuck-order number cannot be trusted to be zero."""
    _UNANSWERED.set(0)


def run_pass(session: Session, reader: HandoffReader, *, now: datetime | None = None) -> list[str]:
    """One pass: read the answers and the orders, write any new answers, set the numbers."""
    now = now or datetime.now(UTC)
    handoff = reader.read()
    raw_answers = handoff.answers if isinstance(handoff.answers, list) else []
    good = [a for a in (check(r) for r in raw_answers) if a is not None]
    _DROPPED.set(len(raw_answers) - len(good))
    written = ingest(session, good)
    _UNANSWERED.set(oldest_unanswered(handoff.orders, {a.order_id for a in good}, now))
    notes = []
    if len(raw_answers) - len(good):
        refused = len(raw_answers) - len(good)
        notes.append(f"{refused} answer(s) refused: not in the expected shape")
    return [*written, *notes] or ["nothing new"]


class KubernetesHandoff:
    """Reads the two ConfigMaps through the Kubernetes API as this program's own ServiceAccount,
    which may `get` these two, by name, and nothing else (templates/cost.yaml). A ConfigMap that
    does not exist yet means nothing has happened, not that something is wrong."""

    def __init__(self, v1: Any, namespace: str, result: str, orders: str) -> None:
        self._v1, self._namespace, self._result, self._orders = v1, namespace, result, orders

    def _data(self, name: str) -> Mapping[str, str] | None:
        from kubernetes.client.exceptions import ApiException

        try:
            return self._v1.read_namespaced_config_map(name, self._namespace).data or {}
        except ApiException as exc:
            if exc.status == 404:
                return None
            raise

    def read(self) -> Handoff:
        return Handoff(
            answers=parse(self._data(self._result), "result.json", "results"),
            orders=parse(self._data(self._orders), "orders.json", "orders"),
        )
