"""`kaval_collector.cost_explorer`: the real bill in, the right signals out, and every doubt a
failure (KAV-92, ADR-0060).

The bill is a recorded one, 14 days of the account's own Cost Explorer answer pulled on 2026-10-18
(`fixtures/bill-2026-10-04-to-17.json`; it names services and dollars, no account details). Nothing
here calls AWS or Kubernetes: the ConfigMap is a small fake with the one method `run_pass` calls.
"""

from __future__ import annotations

import copy
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from kaval_agent import correlate
from kaval_collector import cost_explorer as ce
from kaval_collector.cost_explorer import BillError, Handoff
from kaval_collector.synthetic import COST_EXPLORER
from kaval_shared.models import Signal
from sqlalchemy import func, select
from sqlalchemy.orm import Session

BILL: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures" / "bill-2026-10-04-to-17.json").read_text(encoding="utf-8")
)
# The day after the last day in the recorded bill, when it was pulled.
NOW = datetime(2026, 10, 18, 18, 0, tzinfo=UTC)
TODAY = NOW.date()


def _handoff(bill: object = BILL, *, status: str = "ok", reason: str = "",
             pulled_at: datetime | None = NOW - timedelta(minutes=5),
             calls: int | None = 3) -> Handoff:
    return Handoff(status=status, reason=reason, pulled_at=pulled_at, calls=calls, bill=bill)


class _Reader:
    def __init__(self, handoff: Handoff | None) -> None:
        self.handoff = handoff

    def read(self) -> Handoff | None:
        return self.handoff


def _count(session: Session) -> int:
    return session.scalar(
        select(func.count()).select_from(Signal).where(Signal.source == COST_EXPLORER)) or 0


# ── reading the recorded bill ───────────────────────────────────────────────

def test_the_recorded_bill_parses_into_money_as_decimals() -> None:
    rows = ce.parse_bill(BILL)
    assert rows and all(isinstance(r.amount, Decimal) for r in rows)
    # 14 days, but only the services that cost something on a day: 18 services, most always zero
    assert {r.service for r in rows} == {
        "AWS Cost Explorer", "Amazon EC2 Container Registry (ECR)", "EC2 - Other",
        "Amazon Elastic Compute Cloud - Compute", "Amazon Elastic Container Service",
        "Amazon Simple Storage Service", "Amazon Virtual Private Cloud",
    }
    by = {(r.day, r.service): r.amount for r in rows}
    assert by[(date(2026, 10, 16), "AWS Cost Explorer")] == Decimal("0.1")  # our own test calls
    assert not any(r.amount < ce.MIN_AMOUNT for r in rows)


@pytest.mark.parametrize("body", [
    None, [], {}, {"ResultsByTime": "no"}, {"ResultsByTime": [{}]},
    {"ResultsByTime": [{"TimePeriod": {"Start": "soon"}, "Groups": []}]},
    {"ResultsByTime": [{"TimePeriod": {"Start": "2026-10-01"}, "Groups": "no"}]},
    {"ResultsByTime": [{"TimePeriod": {"Start": "2026-10-01"}, "Groups": [{"Keys": []}]}]},
])
def test_an_answer_of_the_wrong_shape_is_an_error_not_an_empty_bill(body: object) -> None:
    with pytest.raises(BillError):
        ce.parse_bill(body)


def _one(amount: object, unit: object = "USD", keys: object = ("EC2",)) -> dict[str, Any]:
    return {"ResultsByTime": [{"TimePeriod": {"Start": "2026-10-01"}, "Groups": [
        {"Keys": list(keys), "Metrics": {"UnblendedCost": {"Amount": amount, "Unit": unit}}}]}]}  # type: ignore[call-overload]


@pytest.mark.parametrize("body", [
    _one("lots"), _one("NaN"), _one("Infinity"), _one(None), _one("1.00", unit="EUR"),
    _one("1.00", keys=("a", "b")), _one("1.00", keys=("  ",)),
])
def test_money_that_is_not_dollars_or_not_a_number_is_refused(body: object) -> None:
    with pytest.raises(BillError):
        ce.parse_bill(body)


@pytest.mark.parametrize("amount", ["0", "0.0000001", "-0.40"])
def test_zero_dust_and_negative_amounts_write_no_row(amount: str) -> None:
    assert ce.parse_bill(_one(amount)) == []


def test_a_service_name_becomes_a_plain_target() -> None:
    assert ce.target_of("Amazon Elastic Compute Cloud - Compute") == (
        "ce/amazon-elastic-compute-cloud-compute")
    assert ce.target_of("EC2 - Other") == "ce/ec2-other"
    assert ce.target_of("!!!") == "ce/unknown"


# ── judging the handoff ─────────────────────────────────────────────────────

def test_a_fresh_good_handoff_is_good() -> None:
    assert ce.judge(_handoff(), NOW) is None


@pytest.mark.parametrize(("handoff", "code"), [
    (None, "missing"),
    (_handoff(status="failed", reason="ce_call_failed"), "ce_call_failed"),
    (_handoff(status="capped", reason="capped"), "capped"),
    (_handoff(status="failed", reason="the error text AWS sent, with details"), "failed"),
    (_handoff(status=""), "failed"),
    (_handoff(pulled_at=None), "unreadable"),
    (_handoff(bill=None), "unreadable"),
    (_handoff(pulled_at=NOW - ce.STALE_AFTER - timedelta(minutes=1)), "stale"),
    (_handoff(pulled_at=NOW + timedelta(hours=2)), "stale"),  # a clock in the future is a doubt
])
def test_every_doubt_is_named_and_none_looks_like_health(handoff: Handoff | None,
                                                         code: str) -> None:
    assert ce.judge(handoff, NOW) == code


def test_the_handoff_is_read_from_a_configmaps_data() -> None:
    data = {"status": "ok", "pulled_at": "2026-10-18T17:55:00Z", "calls_this_month": "7",
            "bill.json": json.dumps(BILL)}
    h = ce.parse_handoff(data)
    assert h.status == "ok" and h.calls == 7 and h.bill == BILL
    assert h.pulled_at == datetime(2026, 10, 18, 17, 55, tzinfo=UTC)


@pytest.mark.parametrize("data", [
    {}, {"status": "ok"}, {"status": "ok", "pulled_at": "yesterday", "bill.json": "{}"},
    {"status": "ok", "pulled_at": "2026-10-18T17:55:00", "bill.json": "{}"},  # no zone
    {"status": "ok", "pulled_at": "2026-10-18T17:55:00Z", "bill.json": "{not json"},
    {"status": "ok", "pulled_at": "2026-10-18T17:55:00Z", "calls_this_month": "many"},
])
def test_a_half_written_handoff_is_never_a_good_one(data: dict[str, str]) -> None:
    assert ce.judge(ce.parse_handoff(data), NOW) is not None


# ── writing signals (real database) ─────────────────────────────────────────

def test_only_settled_days_are_written(db_session: Session) -> None:
    rows = ce.parse_bill(BILL)
    lines = ce.ingest(db_session, rows, today=TODAY)
    days = {str(s.value["day"]) for s in db_session.scalars(
        select(Signal).where(Signal.source == COST_EXPLORER))}
    # Today is the 18th: the 17th is partial ($0.12 against about $0.70) and must wait.
    assert max(days) == "2026-10-16" and min(days) == "2026-10-04"
    assert len(lines) == _count(db_session) > 0


def test_a_second_pass_over_the_same_bill_writes_nothing(db_session: Session) -> None:
    rows = ce.parse_bill(BILL)
    ce.ingest(db_session, rows, today=TODAY)
    before = _count(db_session)
    assert ce.ingest(db_session, rows, today=TODAY) == []
    assert _count(db_session) == before


def test_a_later_pull_adds_only_the_day_that_settled(db_session: Session) -> None:
    ce.ingest(db_session, ce.parse_bill(BILL), today=TODAY)
    before = _count(db_session)
    later = copy.deepcopy(BILL)  # the 17th, now settled at a normal day's size
    later["ResultsByTime"][-1]["Groups"] = [
        {"Keys": ["EC2 - Other"], "Metrics": {"UnblendedCost": {"Amount": "0.25", "Unit": "USD"}}}]
    lines = ce.ingest(db_session, ce.parse_bill(later), today=TODAY + timedelta(days=1))
    assert len(lines) == 1 and _count(db_session) == before + 1


def test_a_row_has_the_shape_the_correlator_reads(db_session: Session) -> None:
    ce.ingest(db_session, ce.parse_bill(BILL), today=TODAY)
    row = db_session.scalars(select(Signal).where(
        Signal.target == "ce/ec2-other", Signal.value.contains({"day": "2026-10-12"}))).one()
    assert (row.source, row.kind) == ("cost_explorer", "daily_cost")
    # the bill said 0.2537711099; six decimal places are kept, as the proposals table does
    assert row.value == {"service": "EC2 - Other", "granularity": "DAILY", "day": "2026-10-12",
                         "amount_usd": "0.253771"}
    assert row.observed_at == datetime(2026, 10, 12, 12, tzinfo=UTC)
    assert isinstance(row.value["amount_usd"], str)  # money never goes through a float


def test_the_real_bill_raises_no_cost_incident() -> None:
    """The point of the recorded bill: this account's ordinary two weeks, run through the real
    spike rule, must open nothing. Before the dollar floor (correlate.MIN_SPIKE_RISE) a 20-cent
    rise on the compute line was 'three times the usual'."""
    signals = [
        Signal(id=uuid.uuid4(), source="cost_explorer", kind="daily_cost",
               target=ce.target_of(r.service), observed_at=datetime(
                   r.day.year, r.day.month, r.day.day, 12, tzinfo=UTC),
               value={"service": r.service, "granularity": "DAILY", "day": r.day.isoformat(),
                      "amount_usd": str(r.amount)})
        for r in ce.parse_bill(BILL) if r.day <= TODAY - timedelta(days=ce.SETTLED_DAYS)
    ]
    assert len(correlate.cost_spikes(signals)) == 0


def test_a_real_spike_in_the_real_bill_is_found() -> None:
    signals = [
        Signal(id=uuid.uuid4(), source="cost_explorer", kind="daily_cost",
               target=ce.target_of(r.service), observed_at=datetime(
                   r.day.year, r.day.month, r.day.day, 12, tzinfo=UTC),
               value={"service": r.service, "granularity": "DAILY", "day": r.day.isoformat(),
                      "amount_usd": str(r.amount)})
        for r in ce.parse_bill(BILL) if r.day <= TODAY - timedelta(days=ce.SETTLED_DAYS)
    ]
    # a forgotten instance: the usual compute day (about 0.2) becomes $2.40
    signals.append(Signal(
        id=uuid.uuid4(), source="cost_explorer", kind="daily_cost",
        target="ce/amazon-elastic-compute-cloud-compute",
        observed_at=datetime(2026, 10, 17, 12, tzinfo=UTC),
        value={"service": "Amazon Elastic Compute Cloud - Compute", "granularity": "DAILY",
               "day": "2026-10-17", "amount_usd": "2.40"}))
    assert len(correlate.cost_spikes(signals)) == 1


# ── one pass: the health numbers and the loud failures (real database) ─────

def _failing() -> float:
    return float(ce._FAILING._value.get())  # noqa: SLF001 - what the alarm reads


def test_a_good_pass_writes_the_bill_and_clears_the_alarm(db_session: Session) -> None:
    ce._FAILING.set(1)  # noqa: SLF001
    lines = ce.run_pass(db_session, _Reader(_handoff()), now=NOW)
    assert lines and lines != ["nothing new"] and _failing() == 0
    assert ce._LAST_OK._value.get() == (NOW - timedelta(minutes=5)).timestamp()  # noqa: SLF001
    assert ce.run_pass(db_session, _Reader(_handoff()), now=NOW) == ["nothing new"]


@pytest.mark.parametrize(("handoff", "line"), [
    (None, "no good cost pull: missing"),
    (_handoff(status="capped", reason="capped"), "no good cost pull: capped"),
    (_handoff(status="failed", reason="ce_call_failed"), "no good cost pull: ce_call_failed"),
    (_handoff(pulled_at=NOW - timedelta(days=3)), "no good cost pull: stale"),
])
def test_a_bad_pass_writes_nothing_and_raises_the_alarm(
        db_session: Session, handoff: Handoff | None, line: str) -> None:
    ce._FAILING.set(0)  # noqa: SLF001
    assert ce.run_pass(db_session, _Reader(handoff), now=NOW) == [line]
    assert _count(db_session) == 0 and _failing() == 1


def test_a_bill_that_does_not_parse_raises_the_alarm_and_writes_nothing(
        db_session: Session) -> None:
    ce._FAILING.set(0)  # noqa: SLF001
    out = ce.run_pass(db_session, _Reader(_handoff(bill={"ResultsByTime": "no"})), now=NOW)
    assert out[0].startswith("no good cost pull: unreadable")
    assert _count(db_session) == 0 and _failing() == 1


def test_the_call_count_is_shown_even_when_the_pull_failed(db_session: Session) -> None:
    ce.run_pass(db_session, _Reader(_handoff(status="capped", reason="capped", calls=60)), now=NOW)
    assert ce._CALLS._value.get() == 60  # noqa: SLF001
