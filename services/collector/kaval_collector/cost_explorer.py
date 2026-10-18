"""Reads the AWS bill the server's daily cost pull left in the cluster, and writes `daily_cost`
signals (KAV-92, ADR-0060).

The pull itself is not here. A small job on the server (`kaval-cost-pull`, in the node's start-up
script) borrows a login that can only read costs, asks Cost Explorer once, and leaves the answer in
one ConfigMap. This program reads that ConfigMap. So the collector never holds an AWS login: the
cluster's programs have no AWS access at all, and the one program that talks to AWS has one verb.

What it writes is the shape `kaval_agent.correlate` already understands (the synthetic `cost-spike`
scenario uses it): source `cost_explorer`, kind `daily_cost`, one row per service per day, the
amount as a decimal string (money never goes through a float), and `observed_at` on the day the
money was spent. Real bills taught three rules, each of them tested against a recorded one:

- **Only settled days.** Cost Explorer lags: on 2026-10-18 the 17th showed $0.12 against about $0.70
  for a normal day. A partial day written once would stay in the append-only table as a false drop,
  so a day is recorded only when it is at least `SETTLED_DAYS` old. Every day of the current month
  is flagged `Estimated`, so that flag cannot be the test.
- **Once.** The same day for the same service is never written twice, so a retried pull, a pull that
  covers 14 days every time, or a restart changes nothing.
- **Nothing for zero.** A service that cost nothing that day writes no row (about a dozen of the
  account's 18 services are always zero).

Anything the program cannot trust is a failure, never a guess: no ConfigMap, a status other than
`ok`, an answer older than `STALE_AFTER`, or a bill that does not parse. Each sets the health
numbers below, and the rules in `deploy/charts/kaval/files/kaval-cost.rules.yaml` turn them into
an alarm.
Nothing the pull said about *why* it failed is printed or stored beyond a fixed code: AWS's error
text can carry account details, and the health page is readable by every pod.

    python -m kaval_collector.cost_explorer --namespace kaval-staging \
        --handoff kaval-staging-cost-pull
    python -m kaval_collector.cost_explorer ... --every 300   # the deployed shape
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from kaval_shared import metrics
from kaval_shared.models import Signal
from prometheus_client import Gauge
from sqlalchemy import select
from sqlalchemy.orm import Session

from kaval_collector import __version__

SERVICE = "collector-cost"
SOURCE = "cost_explorer"
KIND = "daily_cost"
# A day is final enough to record once it is this many days old (see the module docstring).
SETTLED_DAYS = 2
# Less than a hundredth of a cent is a rounding artefact, not spend.
MIN_AMOUNT = Decimal("0.005")
# The pull runs daily and is retried every few hours, so a good answer older than this means the
# pull has been failing or not running for a day and a half.
STALE_AFTER = timedelta(hours=36)
# The only words the pull may use for what went wrong (the script writes one of these, never text).
REASONS = frozenset({"assume_role_failed", "ce_call_failed", "capped"})

_LAST_OK = Gauge(
    "kaval_cost_pull_last_ok_timestamp_seconds",
    "Unix time of the newest good cost pull this program has read",
)
_FAILING = Gauge(
    "kaval_cost_pull_failing",
    "1 when the newest cost pull is missing, failed, stale or unreadable",
)
_CALLS = Gauge(
    "kaval_cost_calls_this_month", "Cost Explorer requests the server's pull has made this month",
)
_ROWS = Gauge("kaval_cost_rows_written", "Cost rows the last pass added to the signal table")


class BillError(ValueError):
    """The bill is not in the shape Cost Explorer's answer has. Raised, never papered over."""


@dataclass(frozen=True)
class DayCost:
    day: date
    service: str
    amount: Decimal


@dataclass(frozen=True)
class Handoff:
    """What the server's pull left in the ConfigMap. `bill` is the parsed JSON, not yet trusted."""

    status: str
    reason: str
    pulled_at: datetime | None
    calls: int | None
    bill: object | None


class HandoffReader(Protocol):
    """The one call `run_pass` makes. `KubernetesHandoff` and the test fake both satisfy it."""

    def read(self) -> Handoff | None: ...


def _decimal(raw: object) -> Decimal:
    try:
        value = Decimal(str(raw))
    except InvalidOperation as exc:
        raise BillError(f"not an amount: {str(raw)[:20]!r}") from exc
    if not value.is_finite():
        raise BillError(f"not an amount: {str(raw)[:20]!r}")
    return value


def parse_bill(body: object) -> list[DayCost]:
    """Cost Explorer's `get-cost-and-usage` answer (DAILY, grouped by SERVICE, UnblendedCost) ->
    one `DayCost` per service per day that cost something. Strict about the shape, because a bill
    that parses wrongly would write wrong money into a table nothing can edit."""
    if not isinstance(body, Mapping) or not isinstance(body.get("ResultsByTime"), list):
        raise BillError("no ResultsByTime list")
    rows: list[DayCost] = []
    for result in body["ResultsByTime"]:
        try:
            start = date.fromisoformat(str(result["TimePeriod"]["Start"]))
            groups = result["Groups"]
        except (KeyError, TypeError, ValueError) as exc:
            raise BillError("a day without a start date or groups") from exc
        if not isinstance(groups, list):
            raise BillError("groups is not a list")
        for group in groups:
            try:
                (service,) = group["Keys"]
                cost = group["Metrics"]["UnblendedCost"]
                amount, unit = _decimal(cost["Amount"]), cost["Unit"]
            except (KeyError, TypeError, ValueError) as exc:
                if isinstance(exc, BillError):
                    raise
                raise BillError("a group without a service or an amount") from exc
            if unit != "USD":
                raise BillError(f"not dollars: {str(unit)[:10]!r}")
            if not isinstance(service, str) or not service.strip():
                raise BillError("a group with no service name")
            if amount >= MIN_AMOUNT:  # zero, rounding dust and credits' negatives write nothing
                rows.append(DayCost(start, service.strip(), amount))
    return rows


def target_of(service: str) -> str:
    """`Amazon Elastic Compute Cloud - Compute` -> `ce/amazon-elastic-compute-cloud-compute`."""
    return "ce/" + (re.sub(r"[^a-z0-9]+", "-", service.lower()).strip("-") or "unknown")


def _already_written(session: Session, target: str, service: str, day: date) -> bool:
    return session.scalar(
        select(Signal.id)
        .where(Signal.source == SOURCE, Signal.kind == KIND, Signal.target == target,
               Signal.value.contains({"service": service, "day": day.isoformat()}))
        .limit(1)
    ) is not None


def ingest(session: Session, rows: Sequence[DayCost], *, today: date) -> list[str]:
    """Write the settled days not yet in the table. One commit, at the end."""
    cutoff = today - timedelta(days=SETTLED_DAYS)
    written: list[str] = []
    for row in sorted(rows, key=lambda r: (r.day, r.service)):
        if row.day > cutoff:
            continue
        target = target_of(row.service)
        if _already_written(session, target, row.service, row.day):
            continue
        session.add(Signal(
            source=SOURCE, kind=KIND, target=target,
            # Noon UTC of the day the money was spent: the correlator's one-week baseline orders by
            # this, and it must not be the day the row happened to be written.
            observed_at=datetime(row.day.year, row.day.month, row.day.day, 12, tzinfo=UTC),
            value={"service": row.service, "granularity": "DAILY", "day": row.day.isoformat(),
                   "amount_usd": str(row.amount.quantize(Decimal("0.000001")).normalize())},
        ))
        written.append(f"{KIND:12} {row.day} {row.service}  ${row.amount:.4f}")
    session.commit()
    return written


def judge(handoff: Handoff | None, now: datetime) -> str | None:
    """None when the handoff is a good, fresh bill; otherwise a fixed code for what is wrong."""
    if handoff is None:
        return "missing"
    if handoff.status != "ok":
        return handoff.reason if handoff.reason in REASONS else "failed"
    if handoff.pulled_at is None or handoff.bill is None:
        return "unreadable"
    if now - handoff.pulled_at > STALE_AFTER or handoff.pulled_at - now > timedelta(minutes=5):
        return "stale"
    return None


def run_pass(session: Session, reader: HandoffReader, *, now: datetime | None = None) -> list[str]:
    """One pass: read the handoff, set the health numbers, write any new settled days.
    Returns the lines to print; a bad handoff is one line naming the fixed code."""
    now = now or datetime.now(UTC)
    handoff = reader.read()
    if handoff is not None and handoff.calls is not None:
        _CALLS.set(handoff.calls)
    problem = judge(handoff, now)
    if problem is not None:
        _FAILING.set(1)
        return [f"no good cost pull: {problem}"]
    assert handoff is not None and handoff.pulled_at is not None  # judge() ruled out None
    try:
        rows = parse_bill(handoff.bill)
    except BillError as exc:
        _FAILING.set(1)
        return [f"no good cost pull: unreadable ({exc})"]
    written = ingest(session, rows, today=now.date())
    _LAST_OK.set(handoff.pulled_at.timestamp())
    _FAILING.set(0)
    _ROWS.set(len(written))
    return written or ["nothing new"]


class KubernetesHandoff:
    """Reads the ConfigMap through the Kubernetes API as this program's own ServiceAccount, which
    may `get` this one ConfigMap by name and nothing else (templates/cost.yaml)."""

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


def parse_handoff(data: Mapping[str, str]) -> Handoff:
    """ConfigMap data -> `Handoff`. Missing or malformed pieces become None, which `judge` calls
    unreadable: a half-written handoff must look like a failure, never like a good bill."""
    pulled_at: datetime | None = None
    try:
        pulled_at = datetime.fromisoformat(data.get("pulled_at", "").replace("Z", "+00:00"))
        if pulled_at.tzinfo is None:
            pulled_at = None
    except ValueError:
        pulled_at = None
    calls: int | None
    try:
        calls = int(data.get("calls_this_month", ""))
    except ValueError:
        calls = None
    bill: object | None
    try:
        bill = json.loads(data["bill.json"]) if "bill.json" in data else None
    except json.JSONDecodeError:
        bill = None
    return Handoff(status=data.get("status", ""), reason=data.get("reason", ""),
                   pulled_at=pulled_at, calls=calls, bill=bill)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m kaval_collector.cost_explorer",
        description="Read the AWS bill the server's cost pull left in the cluster and write "
                    "daily cost signals.",
    )
    parser.add_argument("--namespace", required=True, help="where Kaval runs")
    parser.add_argument("--handoff", required=True, help="the ConfigMap the cost pull writes")
    parser.add_argument("--every", type=float, metavar="SECONDS",
                        help="keep running, one pass every SECONDS; default: one pass")
    parser.add_argument("--version", action="version", version=f"kaval-collector {__version__}")
    args = parser.parse_args(argv)

    from kaval_shared.db import get_engine  # only a real run needs the database
    from kubernetes import client

    from kaval_collector import k8s
    k8s.load_config()
    reader = KubernetesHandoff(client.CoreV1Api(), args.namespace, args.handoff)

    if args.every:
        metrics.serve(SERVICE, interval=args.every, version=__version__)
        # Young, not stale: the first pull lands minutes after the server starts, and a restart of
        # this program must not look like thirty-six hours without a bill.
        _LAST_OK.set(time.time())
        _FAILING.set(0)
    try:
        while True:
            try:
                with Session(get_engine()) as session:
                    for line in run_pass(session, reader):
                        print(line, flush=True)
                metrics.pass_ok(SERVICE)
            except Exception as exc:  # noqa: BLE001 - one bad pass must not stop the loop
                print(f"cost pass failed: {type(exc).__name__}", file=sys.stderr, flush=True)
                metrics.pass_failed(SERVICE)
                if not args.every:
                    return 1
            if not args.every:
                return 0
            time.sleep(args.every)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
