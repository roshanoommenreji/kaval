"""A pull request's Terraform plan, as a short table that is safe to print in a public log (KAV-89).

    terraform show -json plan.tfplan > plan.json
    python scripts/ops/plan_summary.py <environment> plan.json [base-plan.json]

The repository is public, so a job's log and its summary page are public. A plan's own text names
account numbers (inside every ARN), the state bucket and the alert email, and a plan can carry a
password. So this prints no value at all. For each resource that would change it prints:

  * the resource's address (its name in the code, e.g. `module.node.aws_autoscaling_group.this`),
  * what would happen (add, change, replace, destroy),
  * for a change, the NAMES of the settings that differ, never what they were or would become.

Two more layers sit on top: any twelve-digit number is replaced, and so is every string the caller
passes in `PLAN_SUMMARY_MASKS` (one per line: the bucket name, the email). The detail of a change is
one laptop command away (`terraform plan`); the point here is what a pull request touches.

Reads a file and prints text. It never calls AWS.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

ACCOUNT_ID = re.compile(r"\b\d{12}\b")
MAX_ROWS = 150  # a longer table is cut and says so; the full list is a laptop plan away

# Terraform's action lists, in the order the summary shows them.
ORDER = ("destroy", "replace", "change", "add")


def action_of(actions: list[str]) -> str | None:
    """One word for what a resource_changes entry does, or None when it does nothing."""
    if actions in (["no-op"], ["read"]) or not actions:
        return None
    if "delete" in actions and "create" in actions:
        return "replace"
    if actions == ["create"]:
        return "add"
    if actions == ["delete"]:
        return "destroy"
    return "change"


def changed_names(change: dict[str, Any]) -> list[str]:
    """Names of the settings that differ between before and after (values are never read out)."""
    before = change.get("before") or {}
    after = change.get("after") or {}
    unknown = change.get("after_unknown") or {}
    names = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    names |= {k for k, v in unknown.items() if v}
    return sorted(names)


def mask(text: str, extra: list[str]) -> str:
    """Hide every account number and every string the caller asked to hide."""
    for secret in sorted((s for s in extra if s), key=len, reverse=True):
        text = text.replace(secret, "***")
    return ACCOUNT_ID.sub("<account>", text)


def cell(text: str) -> str:
    """A table cell: no pipe or line break that would break the table."""
    return text.replace("|", "\\|").replace("\n", " ")


Row = tuple[str, str, str]  # (what, address, the setting names)


def rows_of(plan: dict[str, Any]) -> list[Row]:
    """Every resource the plan would touch, worst news first."""
    rows: list[Row] = []
    for rc in plan.get("resource_changes", []):
        what = action_of(rc.get("change", {}).get("actions", []))
        if what is None:
            continue
        settings = changed_names(rc["change"]) if what == "change" else []
        rows.append((what, str(rc.get("address", "?")), ", ".join(settings)))
    rows.sort(key=lambda r: (ORDER.index(r[0]), r[1]))
    return rows


def summarise(
    plan: dict[str, Any],
    env: str,
    masks: list[str] | None = None,
    base: dict[str, Any] | None = None,
) -> str:
    """The markdown for one environment's plan.

    With `base` (the same plan made from the main branch's code against the same record), only what
    the pull request ADDS is the table; a row the base plan has too is "already pending", listed
    apart. That cancels differences that come from the machine (GitHub builds a Lambda's zip file a
    little differently from a laptop, so its fingerprint differs on every plan) and changes
    that were merged but not yet applied, so neither is blamed on the pull request.
    """
    extra = masks or []
    rows = rows_of(plan)
    pending: list[Row] = []
    gone = 0
    if base is not None:
        base_rows = rows_of(base)
        pending = [r for r in rows if r in base_rows]
        gone = sum(1 for r in base_rows if r not in rows)
        rows = [r for r in rows if r not in base_rows]

    counts = {w: sum(1 for r in rows if r[0] == w) for w in ORDER}
    lines = [f"### Terraform plan: `{env}`", ""]
    if not rows:
        lines.append(
            "This pull request adds no change."
            if base is not None
            else "No changes. The code matches Terraform's record."
        )
    else:
        lines.append(
            f"{counts['add']} to add, {counts['change']} to change, "
            f"{counts['replace']} to replace, {counts['destroy']} to destroy."
        )
        lines += ["", "| What | Resource | Settings that change |", "|---|---|---|"]
        for what, address, names in rows[:MAX_ROWS]:
            lines.append(f"| {what} | `{cell(address)}` | {cell(names)} |")
        if len(rows) > MAX_ROWS:
            lines.append(
                f"\n{len(rows) - MAX_ROWS} more not shown; run `terraform plan` for the full list."
            )
    if gone:
        lines += ["", f"This pull request also removes {gone} change(s) that `main` has pending."]
    if pending:
        lines += [
            "",
            "<details><summary>"
            f"{len(pending)} already pending without this pull request (not caused by it)"
            "</summary>",
            "",
            "Seen in the plan of `main` too: a change merged but not yet applied, or a difference "
            "in how GitHub's machine builds a file (a Lambda zip's fingerprint).",
            "",
        ]
        lines += [f"- {what} `{cell(address)}`" for what, address, _ in pending[:MAX_ROWS]]
        lines += ["", "</details>"]
    return mask("\n".join(lines) + "\n", extra)


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4):
        print("usage: plan_summary.py <environment> <plan.json> [base-plan.json]", file=sys.stderr)
        return 2
    env, path = argv[1], argv[2]
    with open(path, encoding="utf-8") as fh:
        plan = json.load(fh)
    base = None
    if len(argv) == 4:
        with open(argv[3], encoding="utf-8") as fh:
            base = json.load(fh)
    masks = os.environ.get("PLAN_SUMMARY_MASKS", "").splitlines()
    sys.stdout.write(summarise(plan, env, masks, base))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
