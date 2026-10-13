"""The rules of `backup_watch.py` (KAV-82, ADR-0049): a dump is owed only when the app server was
up through 19:30 UTC, a parked production is silent, and a missing restore drill is noticed."""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import backup_watch as bw  # noqa: E402
from backup_watch import Dump  # noqa: E402

NOW = datetime(2026, 10, 14, 7, 30, tzinfo=UTC)          # the daily run, 07:30 UTC
LAST_NIGHT = datetime(2026, 10, 13, 19, 30, tzinfo=UTC)  # the boundary just before it
NIGHT_BEFORE = datetime(2026, 10, 12, 19, 30, tzinfo=UTC)
GOOD_SIZE = 4_000_000
RECENT_DRILL = [date(2026, 10, 1)]


def hour(day: int, h: int, m: int = 0) -> datetime:
    return datetime(2026, 10, day, h, m, tzinfo=UTC)


def dump_at(when: datetime, size: int = GOOD_SIZE) -> Dump:
    return Dump(when + timedelta(seconds=40), size)


def up(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    return [(start, end)]


# ── reading the bucket and the history ───────────────────────────────────────────────────────

def test_dumps_are_read_from_their_names_and_the_pointer_file_is_ignored() -> None:
    dumps = bw.parse_dumps([
        {"Key": "postgres/LATEST", "Size": 36},
        {"Key": "postgres/kaval-20261013T193012Z.dump", "Size": 123456},
        {"Key": "postgres/kaval-20261012T193010Z.dump", "Size": 120000},
        {"Key": "postgres/notes.txt", "Size": 5},
    ])
    assert [d.taken for d in dumps] == [datetime(2026, 10, 12, 19, 30, 10, tzinfo=UTC),
                                        datetime(2026, 10, 13, 19, 30, 12, tzinfo=UTC)]
    assert dumps[1].size == 123456


def test_uptime_runs_from_a_launch_to_its_termination_and_an_unended_one_is_still_up() -> None:
    activities = [
        {"StatusCode": "Successful", "Description": "Launching a new EC2 instance: i-0aaa1111",
         "StartTime": "2026-10-13T18:00:00Z", "EndTime": "2026-10-13T18:03:00Z"},
        {"StatusCode": "Successful", "Description": "Terminating EC2 instance: i-0aaa1111",
         "StartTime": "2026-10-13T20:30:00Z", "EndTime": "2026-10-13T20:31:00Z"},
        {"StatusCode": "Successful", "Description": "Launching a new EC2 instance: i-0bbb2222",
         "StartTime": "2026-10-14T06:00:00Z", "EndTime": "2026-10-14T06:02:00Z"},
    ]
    assert bw.uptime_intervals(activities, NOW) == [
        (hour(13, 18, 3), hour(13, 20, 30)), (hour(14, 6, 2), NOW),
    ]


def test_a_launch_that_failed_never_ran() -> None:
    activities = [{"StatusCode": "Failed",
                   "Description": "Launching a new EC2 instance: i-0ccc3333",
                   "StartTime": "2026-10-13T18:00:00Z", "EndTime": "2026-10-13T18:01:00Z"}]
    assert bw.uptime_intervals(activities, NOW) == []


# ── the rule: a dump is owed only when the server was up through 19:30 ────────────────────────

def test_a_parked_production_owes_nothing_and_raises_nothing() -> None:
    assert bw.judge(NOW, [], [], RECENT_DRILL) == []


def test_up_through_the_dump_time_with_a_dump_is_fine() -> None:
    intervals = up(hour(13, 18), hour(13, 20, 30))

    assert bw.judge(NOW, [dump_at(LAST_NIGHT)], intervals, RECENT_DRILL) == []


def test_up_through_the_dump_time_with_no_dump_is_a_problem_that_names_the_night() -> None:
    intervals = up(hour(13, 18), hour(13, 20, 30))

    problems = bw.judge(NOW, [dump_at(NIGHT_BEFORE)], intervals, RECENT_DRILL)

    assert len(problems) == 1
    assert "2026-10-13 19:30 UTC" in problems[0] and "backup-missing.md" in problems[0]


def test_a_server_that_started_a_few_minutes_before_the_dump_time_owes_nothing() -> None:
    # Booted at 19:25: the timer may not have been installed yet, so no alarm.
    intervals = up(hour(13, 19, 25), hour(13, 20, 30))

    assert bw.judge(NOW, [], intervals, RECENT_DRILL) == []


def test_a_server_stopped_before_the_dump_time_owes_nothing() -> None:
    intervals = up(hour(13, 8), hour(13, 17))

    assert bw.judge(NOW, [], intervals, RECENT_DRILL) == []


def test_a_server_stopped_right_at_the_dump_time_owes_nothing() -> None:
    intervals = up(hour(13, 8), hour(13, 19, 35))

    assert bw.judge(NOW, [], intervals, RECENT_DRILL) == []


def test_a_boundary_still_inside_its_grace_period_is_not_yet_missing() -> None:
    soon = LAST_NIGHT + timedelta(minutes=60)
    intervals = up(hour(13, 18), soon + timedelta(hours=1))

    assert bw.missing_dumps(soon, [], intervals) == []
    assert bw.missing_dumps(LAST_NIGHT + bw.GRACE + timedelta(minutes=1), [], intervals) == [
        LAST_NIGHT]


def test_a_dump_from_the_wrong_night_does_not_count() -> None:
    intervals = up(hour(13, 18), hour(13, 20, 30))
    # taken at 03:00, not near 19:30
    wrong = [Dump(hour(13, 3), GOOD_SIZE)]

    assert len(bw.judge(NOW, wrong, intervals, RECENT_DRILL)) == 1


def test_two_missed_nights_are_both_reported_once_each() -> None:
    intervals = up(hour(12, 18), hour(13, 20, 30))  # one long run across both nights

    problems = bw.judge(NOW, [], intervals, RECENT_DRILL)

    assert len(problems) == 2 and "2026-10-12" in problems[0] and "2026-10-13" in problems[1]


def test_the_lookback_does_not_reach_back_for_old_misses() -> None:
    three_days_ago = datetime(2026, 10, 11, 19, 30, tzinfo=UTC)
    intervals = up(three_days_ago - timedelta(hours=2), three_days_ago + timedelta(hours=1))

    assert bw.judge(NOW, [], intervals, RECENT_DRILL) == []


# ── a dump that is there but empty ───────────────────────────────────────────────────────────

def test_a_tiny_newest_dump_is_a_problem_even_though_it_arrived() -> None:
    intervals = up(hour(13, 18), hour(13, 20, 30))

    problems = bw.judge(NOW, [dump_at(LAST_NIGHT, size=1_200)], intervals, RECENT_DRILL)

    assert len(problems) == 1 and "only 1200 bytes" in problems[0] and "cut short" in problems[0]


def test_a_dump_that_shrank_to_under_half_of_the_usual_size_is_a_problem() -> None:
    intervals = up(hour(13, 18), hour(13, 20, 30))
    usual = [dump_at(hour(d, 19, 30), size=24_000) for d in (9, 10, 11, 12)]
    shrunk = dump_at(LAST_NIGHT, size=11_000)

    problems = bw.judge(NOW, [*usual, shrunk], intervals, RECENT_DRILL)

    assert len(problems) == 1 and "under half the usual 24000" in problems[0]


def test_a_normal_variation_in_size_is_not_a_problem() -> None:
    intervals = up(hour(13, 18), hour(13, 20, 30))
    usual = [dump_at(hour(d, 19, 30), size=24_000) for d in (9, 10, 11, 12)]

    assert bw.judge(NOW, [*usual, dump_at(LAST_NIGHT, size=20_000)], intervals, RECENT_DRILL) == []


# ── the restore drill ────────────────────────────────────────────────────────────────────────

def test_a_recent_drill_is_fine_and_a_35_day_old_one_is_still_fine() -> None:
    assert bw.judge(NOW, [], [], [date(2026, 9, 9)]) == []   # exactly 35 days


def test_a_drill_older_than_35_days_is_overdue() -> None:
    problems = bw.judge(NOW, [], [], [date(2026, 9, 8)])

    assert len(problems) == 1 and "36 days ago" in problems[0] and "restore-drill.md" in problems[0]


def test_no_drill_ever_is_a_problem() -> None:
    problems = bw.judge(NOW, [], [], [])

    assert len(problems) == 1 and "ever been recorded" in problems[0]


def test_only_files_named_like_a_drill_record_count() -> None:
    names = ["restore-2026-10-01.md", "README.md", "restore-notes.md", "restore-2026-9-1.md"]

    assert bw.drill_dates(names) == [date(2026, 10, 1)]
