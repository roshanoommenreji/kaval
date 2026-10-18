"""Who spent what: the month's bill by AWS service and by environment. (KAV-93, ADR-0061)

`make cost-showback [MONTH=2026-10]` asks Cost Explorer ONE question ($0.01) and writes
`docs/cost/showback-<month>.md`. It reads usage before credits, like `make cost-report` and the cost
reader, because credits would cancel every dollar.

The environment comes from the `Env` tag, which only appears in the bill once `Env` is activated
as a cost allocation tag in AWS Billing (ADR-0061; the learn page explains why). Spend with no `Env`
label at all is its own column, "no Env label": the number this report exists to drive to
nothing, and it is printed first so it cannot be missed. Some of it can never be labelled
(support charges, taxes, data transfer, the Cost Explorer requests themselves).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DUST = Decimal("0.005")  # under half a cent is rounding, not spend
UNLABELLED = "no Env label"
KNOWN_ENVS = ("prod", "staging", "dev", "state")


class BillError(ValueError):
    """The answer is not in the shape Cost Explorer's is. Raised, never papered over."""


def parse(answer: object) -> dict[str, dict[str, Decimal]]:
    """{service: {environment column: dollars}} from get-cost-and-usage (SERVICE, then Env)."""
    try:
        groups = answer["ResultsByTime"][0]["Groups"]  # type: ignore[index]
        table: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
        for g in groups:
            service, tag = g["Keys"]
            env = tag.split("$", 1)[1]  # "Env$prod" -> "prod"; "Env$" (no label) -> ""
            column = env if env in KNOWN_ENVS else (UNLABELLED if env == "" else f"other: {env}")
            table[service][column] += Decimal(g["Metrics"]["UnblendedCost"]["Amount"])
    except (KeyError, IndexError, TypeError, ValueError, ArithmeticError) as exc:
        raise BillError(f"unexpected cost answer: {type(exc).__name__}") from exc
    return {s: dict(c) for s, c in table.items()}


def columns(table: dict[str, dict[str, Decimal]]) -> list[str]:
    seen = {c for row in table.values() for c in row}
    return (
        [UNLABELLED]
        + [e for e in KNOWN_ENVS if e in seen]
        + sorted(c for c in seen if c != UNLABELLED and c not in KNOWN_ENVS)
    )


def totals(table: dict[str, dict[str, Decimal]]) -> dict[str, Decimal]:
    out: dict[str, Decimal] = defaultdict(Decimal)
    for row in table.values():
        for column, amount in row.items():
            out[column] += amount
    return dict(out)


def render(month: str, table: dict[str, dict[str, Decimal]], today: date) -> str:
    cols = columns(table)
    by_col = totals(table)
    grand = sum(by_col.values(), Decimal(0))
    unlabelled = by_col.get(UNLABELLED, Decimal(0))
    share = (unlabelled / grand * 100) if grand else Decimal(0)
    rows = sorted(((s, sum(r.values(), Decimal(0))) for s, r in table.items()), key=lambda x: -x[1])
    lines = [
        f"# Show-back for {month}",
        "",
        f"Generated {today.isoformat()} by `make cost-showback` (`scripts/ops/showback.py`). Usage",
        "before credits, so the figures are what the month would cost without them. The current",
        "month is an estimate and moves until it closes. Do not edit by hand.",
        "",
        f"**Total ${grand:.2f}. Spend with no `Env` label: ${unlabelled:.2f} ({share:.0f} %).**",
        "",
        "| AWS service | " + " | ".join(cols) + " | Total |",
        "|---|" + "---:|" * (len(cols) + 1),
    ]
    for service, total in rows:
        if total < DUST:
            continue
        cells = " | ".join(f"{table[service].get(c, Decimal(0)):.2f}" for c in cols)
        lines.append(f"| {service} | {cells} | {total:.2f} |")
    cells = " | ".join(f"**{by_col.get(c, Decimal(0)):.2f}**" for c in cols)
    lines += [f"| **Total** | {cells} | **{grand:.2f}** |", ""]
    return "\n".join(lines)


def _month_range(month: str) -> tuple[str, str]:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise SystemExit("MONTH must look like 2026-10")
    year, mon = int(month[:4]), int(month[5:])
    end = date(year + (mon == 12), 1 if mon == 12 else mon + 1, 1)
    return f"{month}-01", end.isoformat()


def fetch(month: str) -> object:
    start, end = _month_range(month)
    done = subprocess.run(
        [
            "aws",
            "ce",
            "get-cost-and-usage",
            "--time-period",
            f"Start={start},End={end}",
            "--granularity",
            "MONTHLY",
            "--metrics",
            "UnblendedCost",
            "--filter",
            '{"Not":{"Dimensions":{"Key":"RECORD_TYPE","Values":["Credit","Refund"]}}}',
            "--group-by",
            "Type=DIMENSION,Key=SERVICE",
            "Type=TAG,Key=Env",
            "--output",
            "json",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if done.returncode != 0:
        raise SystemExit(
            "cost request failed: " + re.sub(r"\d{12}", "<acct>", done.stderr.strip())[:300]
        )
    return json.loads(done.stdout)


def main(argv: list[str]) -> int:
    month = argv[0] if argv else date.today().strftime("%Y-%m")
    text = render(month, parse(fetch(month)), date.today())
    target = ROOT / "docs" / "cost" / f"showback-{month}.md"
    target.write_text(text, encoding="utf-8", newline="\n")
    print(text)
    print(f"written: {target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
