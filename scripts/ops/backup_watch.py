"""Is the nightly database dump being taken, and has a restore been drilled lately?
(KAV-82, ADR-0049)

Run daily by `.github/workflows/backup-watch.yml`, from GitHub and not from AWS, so it still works
when everything in AWS is parked or broken. It only READS: the bucket's listing and the auto-scaling
group's history, with a role that can do nothing else (`infra/modules/backup-watch`).

The difficulty is that production is parked most of the time, and a parked production takes no dump
and should not raise an alarm. So the rule is not "the newest dump is old". It is:

  for each 19:30 UTC in the last two days at which the app server was RUNNING (read from the
  auto-scaling group's launch and terminate history), a dump must exist from the hour after it.

The dump is taken by a timer on the app server (ADR-0038), so "the app server was up through 19:30"
is exactly the condition under which a dump is owed. A server that was not up owes nothing.

Also checked: the newest dump is not suspiciously small (an empty or schema-only dump reads back
fine
and restores to nothing), and the newest restore-drill record (`docs/drills/restore-YYYY-MM-DD.md`)
is not older than 35 days.

The decisions are pure functions with tests (test_backup_watch.py); `main` only fetches and prints.
Exit status 1 means "tell a person", which the workflow turns into a GitHub issue.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

DUMP_TIME = (19, 30)             # UTC, the timer in infra/modules/node/user_data.sh.tftpl
LOOKBACK = timedelta(hours=50)   # two nightly boundaries, whatever time the daily run lands
GRACE = timedelta(minutes=90)    # a dump may land this long after its boundary before "missing"
UP_BEFORE = timedelta(minutes=20)  # the server must have been running this long before 19:30...
UP_AFTER = timedelta(minutes=10)   # ...and for this long after, for the timer to have fired
# Below this a dump is truncated (an empty schema alone is about 20 KB, production's whole
# dump 24 KB).
MIN_DUMP_BYTES = 10_000
SHRUNK_TO = 0.5                  # a newest dump under half the typical size of the last few
DRILL_EVERY = timedelta(days=35)

_DUMP_KEY = re.compile(r"^postgres/[\w-]+-(\d{8}T\d{6}Z)\.dump$")
_INSTANCE = re.compile(r"\b(i-[0-9a-f]{8,17})\b")


@dataclass(frozen=True)
class Dump:
    taken: datetime
    size: int


def parse_dumps(objects: Sequence[Mapping[str, object]]) -> list[Dump]:
    """Dumps from a bucket listing (`Key`, `Size`). The time comes from the key, which the dump
    script writes in UTC, so it does not depend on the object's upload time. `LATEST` and anything
    else in the folder is ignored."""
    dumps: list[Dump] = []
    for obj in objects:
        match = _DUMP_KEY.match(str(obj.get("Key", "")))
        if match:
            taken = datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
            dumps.append(Dump(taken, int(str(obj.get("Size", 0)))))
    return sorted(dumps, key=lambda d: d.taken)


def _when(value: object) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def uptime_intervals(
    activities: Sequence[Mapping[str, object]], now: datetime,
) -> list[tuple[datetime, datetime]]:
    """When the app server was running, from the auto-scaling group's history. A launch starts an
    interval for that instance and a termination ends it; an instance never terminated is still
    up. Unsuccessful activities are ignored: an instance that failed to launch never ran."""
    started: dict[str, datetime] = {}
    ended: dict[str, datetime] = {}
    for act in activities:
        if act.get("StatusCode") != "Successful":
            continue
        found = _INSTANCE.search(str(act.get("Description", "")))
        if not found:
            continue
        instance = found.group(1)
        description = str(act.get("Description", ""))
        if description.startswith("Launching"):
            at = _when(act.get("EndTime")) or _when(act.get("StartTime"))
            if at:
                started[instance] = at
        elif description.startswith("Terminating"):
            at = _when(act.get("StartTime"))
            if at:
                ended[instance] = at
    return sorted((start, ended.get(instance, now)) for instance, start in started.items())


def boundaries(now: datetime) -> list[datetime]:
    """Every 19:30 UTC in the lookback window whose grace period has already passed."""
    out: list[datetime] = []
    day = (now - LOOKBACK).date()
    while day <= now.date():
        boundary = datetime(day.year, day.month, day.day, *DUMP_TIME, tzinfo=UTC)
        if now - LOOKBACK <= boundary and boundary + GRACE <= now:
            out.append(boundary)
        day += timedelta(days=1)
    return out


def was_up(boundary: datetime, intervals: list[tuple[datetime, datetime]]) -> bool:
    return any(start <= boundary - UP_BEFORE and end >= boundary + UP_AFTER
               for start, end in intervals)


def missing_dumps(
    now: datetime, dumps: list[Dump], intervals: list[tuple[datetime, datetime]],
) -> list[datetime]:
    """The 19:30 boundaries at which the app server was up but no dump followed."""
    return [
        b for b in boundaries(now)
        if was_up(b, intervals)
        and not any(b - timedelta(minutes=5) <= d.taken <= b + GRACE for d in dumps)
    ]


def drill_dates(names: list[str]) -> list[date]:
    out: list[date] = []
    for name in names:
        match = re.fullmatch(r"restore-(\d{4}-\d{2}-\d{2})\.md", name)
        if match:
            out.append(date.fromisoformat(match.group(1)))
    return sorted(out)


def judge(
    now: datetime, dumps: list[Dump], intervals: list[tuple[datetime, datetime]],
    drills: list[date],
) -> list[str]:
    """What is wrong, in plain words; empty means all is well."""
    problems = [
        f"The app server was running at {b:%Y-%m-%d %H:%M} UTC, when the nightly dump is due, "
        "but no dump was taken. Runbook: docs/runbooks/backup-missing.md"
        for b in missing_dumps(now, dumps, intervals)
    ]
    if dumps and dumps[-1].size < MIN_DUMP_BYTES:
        problems.append(
            f"The newest dump ({dumps[-1].taken:%Y-%m-%d %H:%M} UTC) is only {dumps[-1].size} "
            f"bytes, far smaller than a real one; it may be cut short. "
            "Runbook: docs/runbooks/backup-missing.md")
    elif len(dumps) >= 3:
        before = sorted(d.size for d in dumps[-6:-1])
        typical = before[len(before) // 2]
        if dumps[-1].size < typical * SHRUNK_TO:
            problems.append(
                f"The newest dump ({dumps[-1].taken:%Y-%m-%d %H:%M} UTC) is "
                f"{dumps[-1].size} bytes, "
                f"under half the usual {typical}; data may be missing from it. "
                "Runbook: docs/runbooks/backup-missing.md")
    if not drills:
        problems.append("No restore drill has ever been recorded (docs/drills/). "
                        "Runbook: docs/runbooks/restore-drill.md")
    elif now.date() - drills[-1] > DRILL_EVERY:
        problems.append(
            f"The last restore drill was {(now.date() - drills[-1]).days} days ago "
            f"({drills[-1]}); one is due every {DRILL_EVERY.days} days. "
            "Runbook: docs/runbooks/restore-drill.md")
    return problems


def _aws(*args: str) -> object:
    out = subprocess.run(["aws", *args, "--output", "json"], check=True, capture_output=True,
                         text=True, encoding="utf-8").stdout
    return json.loads(out) if out.strip() else None


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: backup_watch.py <bucket> <auto-scaling-group> <drills-folder>")
        return 2
    bucket, group, drills_dir = argv[1], argv[2], Path(argv[3])
    now = datetime.now(UTC)
    listing = _aws("s3api", "list-objects-v2", "--bucket", bucket, "--prefix", "postgres/")
    objects = listing.get("Contents", []) if isinstance(listing, dict) else []
    history = _aws("autoscaling", "describe-scaling-activities",
                   "--auto-scaling-group-name", group, "--max-items", "200")
    activities = history.get("Activities", []) if isinstance(history, dict) else []
    dumps = parse_dumps(objects)
    intervals = uptime_intervals(activities, now)
    names = [p.name for p in drills_dir.glob("restore-*.md")] if drills_dir.is_dir() else []
    drills = drill_dates(names)
    print(f"dumps seen: {len(dumps)}; newest: {dumps[-1].taken:%Y-%m-%d %H:%M}Z" if dumps
          else "dumps seen: 0")
    print(f"app-server runs in the history: {len(intervals)}; "
          f"boundaries checked: {[f'{b:%m-%d %H:%M}' for b in boundaries(now)]}")
    print(f"restore drills recorded: {len(drills)}; newest: {drills[-1] if drills else 'none'}")
    problems = judge(now, dumps, intervals, drills)
    for problem in problems:
        print(f"PROBLEM: {problem}")
    if not problems:
        print("OK: no dump owed is missing, the newest is a plausible size, a drill is recent.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
