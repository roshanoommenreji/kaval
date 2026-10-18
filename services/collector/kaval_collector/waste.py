"""Reads the daily waste scan the server left in the cluster, and writes one signal per finding
(KAV-94, ADR-0062).

The scan itself is not here, and neither is any AWS login. A small function outside the cluster
(infra/modules/waste-scan) looks at the account once a day and leaves its findings in one
parameter; the server's timer copies that into one ConfigMap; this reads the ConfigMap. It runs in
the same pod as the cost reader (`kaval_collector.cost_explorer`) and follows the same rules:

- **Anything it cannot trust is a failure, never a guess**: no ConfigMap, a scan that says it
  failed, a scan older than `STALE_AFTER`, or a document that does not parse. Each sets the health
  numbers below, and `deploy/charts/kaval/files/kaval-cost.rules.yaml` turns them into an alarm. A
  scan that has not run yet (`pending`, a new environment's first day) is waiting, not failing.
- **Only what it recognises is written.** The ConfigMap is the one place a person or a bug could put
  arbitrary text in front of a model, so every finding is rebuilt from a whitelist: a known kind, a
  target made from an AWS id, and only the named fields, each checked against a fixed shape. A
  finding that does not fit is dropped and counted, never stored as it came.
- **Once per scan.** The scan repeats a finding every day for as long as it is true; this writes it
  once per scan, so a retried pass or a restart changes nothing, and each day's row keeps the
  incident open until the waste is gone (the correlator closes it after 48 quiet hours).

What the findings become is the correlator's business (`kaval_shared.health.WASTE_SIGNALS`): most
are an incident for a person, with no fix proposed. Two (a loose disk, an old backup copy) can be
removed, in steps, by the planner (KAV-95, ADR-0063). The removal worker's answers are read in
`kaval_collector.removal`.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from kaval_shared.models import Signal
from prometheus_client import Gauge
from sqlalchemy import select
from sqlalchemy.orm import Session

SOURCE = "aws_ec2"
# The scan runs daily and is retried within the hour, so a good one older than this means it has
# been failing or not running for a day and a half.
STALE_AFTER = timedelta(hours=36)
REASONS = frozenset({"describe_failed"})
ENVS = frozenset({"prod", "staging", "dev", "state"})
REQUIRED_KEYS = ("Project", "Env", "ManagedBy")

_ID = re.compile(r"[a-z]+-[0-9a-f]{8,17}")
_MONEY = re.compile(r"\d{1,6}\.\d{2}")
_WORD = re.compile(r"[a-z0-9_]{1,16}")
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")

_LAST_OK = Gauge(
    "kaval_waste_scan_last_ok_timestamp_seconds",
    "Unix time of the newest good waste scan this program has read",
)
_FAILING = Gauge(
    "kaval_waste_scan_failing",
    "1 when the newest waste scan is missing, failed, stale or unreadable",
)
# No label on purpose: the metrics page is readable by every pod and carries only the fixed labels
# services/shared/tests/test_metrics.py allows. What was found, by kind, is in the signal table.
_FINDINGS = Gauge("kaval_waste_findings", "Findings in the newest good waste scan")
_DROPPED = Gauge(
    "kaval_waste_findings_dropped",
    "Findings in the newest scan this program refused because they were not in the expected shape",
)


def start() -> None:
    """Young, not stale: the first scan lands minutes after the server starts, and a restart of this
    program must not look like thirty-six hours without one."""
    _LAST_OK.set(time.time())
    _FAILING.set(0)


def pass_failed() -> None:
    """The pass itself broke (not the scan): the alarm must say so."""
    _FAILING.set(1)


def _id(prefix: str) -> Callable[[object], bool]:
    return lambda v: (
        isinstance(v, str) and bool(_ID.fullmatch(v)) and (not prefix or v.startswith(prefix + "-"))
    )


def _int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 10_000_000


def _one_of(*words: str) -> Callable[[object], bool]:
    return lambda v: isinstance(v, str) and v in words


def _word(v: object) -> bool:
    return isinstance(v, str) and bool(_WORD.fullmatch(v))


def _money(v: object) -> bool:
    return isinstance(v, str) and bool(_MONEY.fullmatch(v))


def _stamp(v: object) -> bool:
    return isinstance(v, str) and bool(_STAMP.fullmatch(v))


def _keys(v: object) -> bool:
    return (
        isinstance(v, list)
        and 1 <= len(v) <= len(REQUIRED_KEYS)
        and all(isinstance(k, str) and k in REQUIRED_KEYS for k in v)
    )


# kind -> (target prefix, the field that holds the id the target is built from,
#          {field: check}, optional fields). Nothing outside this table is ever stored.
Shape = tuple[str, str, dict[str, Callable[[object], bool]], frozenset[str]]
SHAPES: dict[str, Shape] = {
    "volume_unattached": (
        "ebs/",
        "volume_id",
        {
            "volume_id": _id("vol"),
            "state": _one_of("available"),
            "size_gib": _int,
            "volume_type": _word,
            "days_unattached": _int,
            "monthly_cost_usd": _money,
            # Where the disk stands in the removal steps (KAV-95): read from the labels on AWS.
            "pending_removal_since": _stamp,
            "backup_snapshot_id": _id("snap"),
            "backup_state": _one_of("completed", "pending", "error", "unknown"),
        },
        frozenset(
            {"monthly_cost_usd", "pending_removal_since", "backup_snapshot_id", "backup_state"}
        ),
    ),
    "address_unattached": (
        "eip/",
        "allocation_id",
        {
            "allocation_id": _id("eipalloc"),
            "days_unattached": _int,
            "monthly_cost_usd": _money,
        },
        frozenset(),
    ),
    "snapshot_past_retention": (
        "snapshot/",
        "snapshot_id",
        {
            "snapshot_id": _id("snap"),
            "class": _one_of("stop_copy", "manual", "unlabelled"),
            "age_days": _int,
            "limit_days": _int,
            "size_gib": _int,
        },
        frozenset(),
    ),
    "server_overstay": (
        "ec2/",
        "instance_id",
        {
            "instance_id": _id("i"),
            "env": _one_of(*sorted(ENVS)),
            "running_hours": _int,
            "limit_hours": _int,
        },
        frozenset(),
    ),
    "resource_unlabelled": (
        "*",
        "resource_id",
        {
            "resource_type": _one_of("instance", "volume", "snapshot", "address"),
            "resource_id": _id(""),
            "problems": _keys,
        },
        frozenset(),
    ),
}
# resource_unlabelled's target depends on what kind of resource it is
_UNLABELLED_TARGET = {
    "instance": "ec2/",
    "volume": "ebs/",
    "snapshot": "snapshot/",
    "address": "eip/",
}
_UNLABELLED_ID = {"instance": "i-", "volume": "vol-", "snapshot": "snap-", "address": "eipalloc-"}


@dataclass(frozen=True)
class Finding:
    kind: str
    target: str
    value: dict[str, Any]


@dataclass(frozen=True)
class Handoff:
    """What the server left in the ConfigMap. `findings` is parsed JSON, not yet trusted."""

    status: str
    reason: str
    scanned_at: datetime | None
    findings: object | None
    truncated: int


class HandoffReader(Protocol):
    def read(self) -> Handoff | None: ...


def check(raw: object) -> Finding | None:
    """One finding rebuilt from the whitelist, or None if any part of it is not as expected."""
    if not isinstance(raw, Mapping):
        return None
    kind, value = raw.get("kind"), raw.get("value")
    if not isinstance(kind, str) or kind not in SHAPES or not isinstance(value, Mapping):
        return None
    prefix, id_field, fields, optional = SHAPES[kind]
    clean: dict[str, Any] = {}
    for name, ok in fields.items():
        if name not in value:
            if name in optional:
                continue
            return None
        if not ok(value[name]):
            return None
        clean[name] = value[name]
    if prefix == "*":
        if not clean["resource_id"].startswith(_UNLABELLED_ID[clean["resource_type"]]):
            return None
        prefix = _UNLABELLED_TARGET[clean["resource_type"]]
    target = prefix + str(clean[id_field])
    if raw.get("target") != target:
        return None
    return Finding(kind, target, clean)


def parse_handoff(data: Mapping[str, str]) -> Handoff:
    """ConfigMap data -> `Handoff`. A half-written or malformed document becomes one `judge` calls
    unreadable: it must look like a failure, never like a clean account."""
    try:
        doc = json.loads(data["scan.json"])
        if not isinstance(doc, dict):
            raise ValueError
    except (KeyError, ValueError):
        return Handoff("", "", None, None, 0)
    scanned_at: datetime | None
    try:
        scanned_at = datetime.fromisoformat(str(doc.get("scanned_at", "")).replace("Z", "+00:00"))
        if scanned_at.tzinfo is None:
            scanned_at = None
    except ValueError:
        scanned_at = None
    truncated = doc.get("truncated")
    return Handoff(
        status=str(doc.get("status", "")),
        reason=str(doc.get("reason", "")),
        scanned_at=scanned_at,
        findings=doc.get("findings"),
        truncated=truncated
        if isinstance(truncated, int) and not isinstance(truncated, bool)
        else 0,
    )


def judge(handoff: Handoff | None, now: datetime) -> str | None:
    """None when the scan is good and fresh; `pending` when no scan has run yet; otherwise a fixed
    code for what is wrong."""
    if handoff is None:
        return "missing"
    if handoff.status == "pending":
        return "pending"
    if handoff.status != "ok":
        return handoff.reason if handoff.reason in REASONS else "failed"
    if handoff.scanned_at is None or not isinstance(handoff.findings, list):
        return "unreadable"
    if now - handoff.scanned_at > STALE_AFTER or handoff.scanned_at - now > timedelta(minutes=5):
        return "stale"
    return None


def _already_written(session: Session, finding: Finding, scanned_at: str) -> bool:
    return (
        session.scalar(
            select(Signal.id)
            .where(
                Signal.source == SOURCE,
                Signal.kind == finding.kind,
                Signal.target == finding.target,
                Signal.value.contains({"scanned_at": scanned_at}),
            )
            .limit(1)
        )
        is not None
    )


def ingest(session: Session, findings: Sequence[Finding], scanned_at: datetime) -> list[str]:
    """Write the findings of this scan that are not already in the table. One commit."""
    stamp = scanned_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    written: list[str] = []
    for f in findings:
        if _already_written(session, f, stamp):
            continue
        session.add(
            Signal(
                source=SOURCE,
                kind=f.kind,
                target=f.target,
                observed_at=scanned_at.astimezone(UTC),
                value={**f.value, "scanned_at": stamp},
            )
        )
        written.append(f"{f.kind:24} {f.target}")
    session.commit()
    return written


def run_pass(session: Session, reader: HandoffReader, *, now: datetime | None = None) -> list[str]:
    """One pass: read the handoff, set the health numbers, write any new findings. Returns the
    lines to print; a bad handoff is one line naming the fixed code."""
    now = now or datetime.now(UTC)
    handoff = reader.read()
    problem = judge(handoff, now)
    if problem == "pending":
        _FAILING.set(0)  # a new environment's first day: waiting, not failing
        return ["waste scan: none has run yet"]
    if problem is not None:
        _FAILING.set(1)
        return [f"no good waste scan: {problem}"]
    assert handoff is not None and handoff.scanned_at is not None
    assert isinstance(handoff.findings, list)  # judge() ruled out anything else
    good = [f for f in (check(raw) for raw in handoff.findings) if f is not None]
    dropped = len(handoff.findings) - len(good)
    written = ingest(session, good, handoff.scanned_at)
    _LAST_OK.set(handoff.scanned_at.timestamp())
    _FAILING.set(0)
    _DROPPED.set(dropped)
    _FINDINGS.set(len(good))
    notes = []
    if dropped:
        notes.append(f"{dropped} finding(s) refused: not in the expected shape")
    if handoff.truncated:
        notes.append(f"{handoff.truncated} more finding(s) did not fit the scan's hand-over")
    return [*written, *notes] or ["nothing new"]


class KubernetesHandoff:
    """Reads the ConfigMap through the Kubernetes API as this program's own ServiceAccount, which
    may `get` this ConfigMap and the cost one, by name, and nothing else (templates/cost.yaml)."""

    def __init__(self, v1: Any, namespace: str, name: str) -> None:
        self._v1, self._namespace, self._name = v1, namespace, name

    def read(self) -> Handoff | None:
        from kubernetes.client.exceptions import ApiException

        try:
            cm = self._v1.read_namespaced_config_map(self._name, self._namespace)
        except ApiException as exc:
            if exc.status == 404:
                return None
            raise
        return parse_handoff(cm.data or {})
