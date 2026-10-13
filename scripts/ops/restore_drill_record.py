"""Turn a restore drill's measurements into a verdict and a dated record (KAV-82, ADR-0049).

`scripts/ops/restore-drill.sh` does the work (it downloads production's newest dump and restores it
into a throwaway Postgres on this machine, timing each step) and hands the numbers to this file.
This file decides pass or fail and writes `docs/drills/restore-YYYY-MM-DD.md`. The daily backup
check (`backup_watch.py`) reads that folder's file names to notice when a drill is overdue.

What a drill proves: the newest dump can be read back whole (strict restore, no tolerated
errors), it
is for a schema this repository knows, and it contains data. What it deliberately does not do is
replace the database server, which `make db-restore-snapshot` and Lab 27 measured (3 min 51 s) and
`make staging-up` repeats on every release (about 5 minutes for the whole stack). The recovery time
stated in the record adds the two, and the target is in `docs/architecture/disaster-recovery.md`.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

# The stated objectives (docs/architecture/disaster-recovery.md). A drill is judged against them.
RTO_TARGET = timedelta(minutes=30)
RPO_TARGET = timedelta(hours=24)
# Not measured by a drill; taken from the live drills that did measure them (Lab 27, 29, 32).
SERVER_REPLACEMENT = timedelta(minutes=5)
# Tables that must exist in any dump of this schema, and the ones that must hold rows to count as
# "contains data". A dump of an empty database restores perfectly and is useless.
EXPECTED_TABLES = ("signal", "incident", "incident_signal", "proposal", "action", "decision",
                   "execution", "outcome", "alembic_version")
# Tables added by a later migration: required only of a dump whose schema version includes it (an
# older dump legitimately lacks them; found by the first real drill, whose dump predated this one).
ADDED_BY = {"notification": "b7d2e5f1c3a8"}
MUST_HAVE_ROWS = ("signal", "incident")


@dataclass
class Drill:
    key: str
    taken: datetime
    size_bytes: int
    drilled_at: datetime
    download: timedelta
    restore: timedelta
    restore_ok: bool
    alembic_found: str
    tables: dict[str, int]          # table -> row count; a missing table is simply absent


def revisions(migrations_dir: Path) -> tuple[dict[str, str | None], str]:
    """Every migration revision in the repository with its parent, and the newest one (the head)."""
    found: dict[str, str | None] = {}
    for path in migrations_dir.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        rev = re.search(r"^revision(?:: str)? = '([0-9a-f]+)'", text, re.M)
        down = re.search(r"^down_revision(?:: [^=]+)? = (?:'([0-9a-f]+)'|None)", text, re.M)
        if rev:
            found[rev.group(1)] = down.group(1) if down else None
    parents = {d for d in found.values() if d}
    heads = [r for r in found if r not in parents]
    return found, heads[0] if len(heads) == 1 else ""


def _history(found: str, parents: dict[str, str | None]) -> set[str]:
    """`found` and every revision before it."""
    seen: set[str] = set()
    current: str | None = found
    while current and current in parents and current not in seen:
        seen.add(current)
        current = parents[current]
    return seen


def verdict(drill: Drill, parents: dict[str, str | None]) -> list[str]:
    """What is wrong, in plain words; empty means the drill passed."""
    problems: list[str] = []
    if not drill.restore_ok:
        problems.append("the dump did not restore cleanly (the strict restore reported errors)")
    known = set(parents)
    history = _history(drill.alembic_found, parents)
    needed = list(EXPECTED_TABLES) + [t for t, rev in ADDED_BY.items() if rev in history]
    missing = [t for t in needed if t not in drill.tables]
    if missing:
        problems.append(f"tables missing after the restore: {', '.join(missing)}")
    if drill.alembic_found not in known:
        problems.append(f"the dump's schema version ({drill.alembic_found or 'none'}) is not one "
                        "this repository knows")
    if drill.drilled_at - drill.taken > RPO_TARGET * 7:
        problems.append("the newest dump is more than a week old, so this drill proves little "
                        "about last night's backup")
    if drill.download + drill.restore + SERVER_REPLACEMENT > RTO_TARGET:
        problems.append("the estimated recovery time is over the target "
                        f"({RTO_TARGET.total_seconds() / 60:.0f} minutes)")
    return problems


def notes(drill: Drill) -> list[str]:
    """Things worth saying on a passing record: it passed, but proves less than it could. An empty
    dump restores perfectly and proves only the mechanics; production is parked most of the time
    and, when the first drill ran, really was empty, so that is a note and not a failure (a failure
    would make the monthly drill impossible to pass)."""
    empty = [t for t in MUST_HAVE_ROWS if drill.tables.get(t, 0) == 0]
    if empty:
        return [f"The dump held no rows in {', '.join(empty)}, so this drill proves the restore "
                "works but not that real data survives it. Repeat it once production has run "
                "long enough to hold real signals and incidents."]
    return []


def _age_note(drill: Drill) -> list[str]:
    if drill.drilled_at - drill.taken > RPO_TARGET:
        return ["The newest dump is older than the 24-hour target. The dump is taken at 19:30 UTC "
                "only on nights production is running; a parked production takes none. The "
                "volume snapshots (03:00 UTC daily, kept 7, and one before every stop) are the "
                "other copy and are not "
                "affected. See docs/architecture/disaster-recovery.md."]
    return []


def _mins(delta: timedelta) -> str:
    seconds = int(delta.total_seconds())
    if seconds >= 3600:
        return f"{seconds // 3600} h {seconds % 3600 // 60} min"
    return f"{seconds // 60} min {seconds % 60} s" if seconds >= 60 else f"{seconds} s"


def render(drill: Drill, problems: list[str], head: str, parents: dict[str, str | None]) -> str:
    known = set(parents)
    age = drill.drilled_at - drill.taken
    recovery = drill.download + drill.restore + SERVER_REPLACEMENT
    behind = "this repository's newest" if drill.alembic_found == head else (
        "an older one this repository knows" if drill.alembic_found in known else "UNKNOWN")
    shown = [t for t in [*EXPECTED_TABLES, *ADDED_BY] if t in drill.tables]
    rows = "\n".join(f"| `{t}` | {drill.tables[t]} |" for t in shown)
    caveats = "".join(f"\n\n> **Note:** {n}" for n in [*notes(drill), *_age_note(drill)])
    result = ("**PASS**" + caveats if not problems
              else "**FAIL**\n\n" + "\n".join(f"- {p}" for p in problems))
    return f"""# Restore drill — {drill.drilled_at:%Y-%m-%d}

Made by `scripts/ops/restore-drill.sh` ([runbook](../runbooks/restore-drill.md),
[ADR-0049](../adr/0049-backups-watched-and-restores-drilled.md)). Production was only **read**; the
restore went into a throwaway Postgres on this machine, which is deleted afterwards.

Result: {result}

| | |
|---|---|
| Dump restored | `{drill.key}` ({drill.size_bytes / 1_000_000:.1f} MB) |
| Dump taken | {drill.taken:%Y-%m-%d %H:%M} UTC |
| Drill run | {drill.drilled_at:%Y-%m-%d %H:%M} UTC |
| **Data that would have been lost** (dump age at the drill) | **{_mins(age)}** (target: at most {_mins(RPO_TARGET)}) |
| Download | {_mins(drill.download)} |
| Strict restore, no errors tolerated | {_mins(drill.restore)}, {"clean" if drill.restore_ok else "ERRORS"} |
| Replace the database server (not measured here: Lab 27, `make staging-up`) | about {_mins(SERVER_REPLACEMENT)} |
| **Estimated recovery time** | **{_mins(recovery)}** (target: at most {_mins(RTO_TARGET)}) |
| Schema version in the dump | `{drill.alembic_found}`, {behind} |

Rows found after the restore:

| Table | Rows |
|---|---|
{rows}
"""  # noqa: E501 (markdown table rows)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--key", required=True)
    parser.add_argument("--taken", required=True, help="ISO time the dump was taken, UTC")
    parser.add_argument("--bytes", type=int, required=True)
    parser.add_argument("--download-seconds", type=float, required=True)
    parser.add_argument("--restore-seconds", type=float, required=True)
    parser.add_argument("--restore-ok", choices=["yes", "no"], required=True)
    parser.add_argument("--alembic", required=True)
    parser.add_argument("--tables", required=True, help="table=rows,table=rows")
    parser.add_argument("--migrations", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    parents, head = revisions(args.migrations)
    tables = {k: int(v) for k, v in (p.split("=") for p in args.tables.split(",") if "=" in p)}
    drill = Drill(
        key=args.key, taken=datetime.fromisoformat(args.taken.replace("Z", "+00:00")),
        size_bytes=args.bytes, drilled_at=datetime.now(UTC),
        download=timedelta(seconds=args.download_seconds),
        restore=timedelta(seconds=args.restore_seconds), restore_ok=args.restore_ok == "yes",
        alembic_found=args.alembic, tables=tables,
    )
    problems = verdict(drill, parents)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    # Only a passing drill is named like one, so a failed attempt never satisfies the daily
    # check's "a drill was done in the last 35 days".
    stem = "restore" if not problems else "restore-failed"
    path = args.out_dir / f"{stem}-{drill.drilled_at:%Y-%m-%d}.md"
    path.write_text(render(drill, problems, head, parents), encoding="utf-8", newline="\n")
    print(f"record written: {path}")
    print("PASS" if not problems else "FAIL: " + "; ".join(problems))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
