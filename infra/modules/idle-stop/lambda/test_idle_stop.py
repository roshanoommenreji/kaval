"""The idle decision is the only logic worth testing off-AWS: everything else is API calls."""

from datetime import UTC, datetime, timedelta

from idle_stop import decide

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
FOUR_HOURS = 4 * 3600


def test_nothing_running_is_never_idle() -> None:
    assert decide(NOW, [], [], FOUR_HOURS)["idle"] is False


def test_fresh_launch_is_not_idle() -> None:
    # A node that came up a minute ago must not be reaped before its first use.
    assert decide(NOW, [NOW - timedelta(minutes=1)], [], FOUR_HOURS)["idle"] is False


def test_old_launch_with_no_activity_is_idle() -> None:
    verdict = decide(NOW, [NOW - timedelta(hours=5)], [], FOUR_HOURS)
    assert verdict["idle"] is True
    assert verdict["idle_seconds"] == 5 * 3600


def test_recent_activity_resets_the_clock() -> None:
    launched = NOW - timedelta(hours=9)
    touched = NOW - timedelta(minutes=30)
    assert decide(NOW, [launched], [touched], FOUR_HOURS)["idle"] is False


def test_the_boundary_is_inclusive() -> None:
    assert decide(NOW, [NOW - timedelta(seconds=FOUR_HOURS)], [], FOUR_HOURS)["idle"] is True
    assert decide(NOW, [NOW - timedelta(seconds=FOUR_HOURS - 1)], [], FOUR_HOURS)["idle"] is False


def test_newest_event_wins_across_node_and_database() -> None:
    # The database was started later than the node; the later one is the floor.
    launches = [NOW - timedelta(hours=6), NOW - timedelta(hours=1)]
    assert decide(NOW, launches, [], FOUR_HOURS)["idle"] is False


def test_future_timestamps_do_not_go_negative() -> None:
    # Clock skew between EC2 and the Lambda must not produce a negative idle time.
    assert decide(NOW, [NOW + timedelta(seconds=5)], [], FOUR_HOURS)["idle_seconds"] == 0
