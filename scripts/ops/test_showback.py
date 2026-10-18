"""The show-back report's arithmetic (KAV-93), on a made-up Cost Explorer answer: no AWS call."""

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import showback as sb  # noqa: E402


def group(service: str, tag: str, amount: str) -> dict[str, Any]:
    return {"Keys": [service, tag], "Metrics": {"UnblendedCost": {"Amount": amount, "Unit": "USD"}}}


ANSWER = {
    "ResultsByTime": [
        {
            "Groups": [
                group("Amazon Elastic Compute Cloud - Compute", "Env$prod", "10.00"),
                group("Amazon Elastic Compute Cloud - Compute", "Env$staging", "2.50"),
                group("Amazon Elastic Compute Cloud - Compute", "Env$", "0.50"),
                group("AWS Cost Explorer", "Env$", "0.10"),
                group("Amazon Simple Storage Service", "Env$qa", "0.30"),
                group("Tax", "Env$", "0.001"),
            ]
        }
    ]
}


def test_spend_is_split_by_service_and_environment() -> None:
    table = sb.parse(ANSWER)
    ec2 = table["Amazon Elastic Compute Cloud - Compute"]
    assert ec2 == {
        "prod": Decimal("10.00"),
        "staging": Decimal("2.50"),
        sb.UNLABELLED: Decimal("0.50"),
    }


def test_an_unknown_environment_is_shown_not_hidden() -> None:
    assert sb.parse(ANSWER)["Amazon Simple Storage Service"] == {"other: qa": Decimal("0.30")}


def test_the_unlabelled_share_leads_the_report() -> None:
    text = sb.render("2026-10", sb.parse(ANSWER), date(2026, 10, 18))
    assert "Total $13.40. Spend with no `Env` label: $0.60 (4 %)." in text
    assert "| AWS service | no Env label | prod | staging | other: qa | Total |" in text


def test_rows_are_biggest_first_and_dust_is_left_out() -> None:
    text = sb.render("2026-10", sb.parse(ANSWER), date(2026, 10, 18))
    assert text.index("Compute") < text.index("Simple Storage") < text.index("Cost Explorer")
    assert "| Tax |" not in text  # $0.001 is rounding


def test_an_empty_bill_does_not_divide_by_zero() -> None:
    text = sb.render("2026-10", sb.parse({"ResultsByTime": [{"Groups": []}]}), date(2026, 10, 18))
    assert "Total $0.00" in text and "(0 %)" in text


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"ResultsByTime": []},
        {"ResultsByTime": [{"Groups": [{"Keys": ["a"]}]}]},
        {"ResultsByTime": [{"Groups": [group("s", "noenv", "1")]}]},
        {"ResultsByTime": [{"Groups": [group("s", "Env$prod", "lots")]}]},
    ],
)
def test_an_answer_in_the_wrong_shape_is_refused(bad: object) -> None:
    with pytest.raises(sb.BillError):
        sb.parse(bad)


def test_the_month_range_handles_december_and_refuses_nonsense() -> None:
    assert sb._month_range("2026-12") == ("2026-12-01", "2027-01-01")
    assert sb._month_range("2026-10") == ("2026-10-01", "2026-11-01")
    with pytest.raises(SystemExit):
        sb._month_range("October")
