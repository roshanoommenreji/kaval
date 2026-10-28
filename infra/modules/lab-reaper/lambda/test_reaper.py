"""The timer's decision is the only logic worth testing off-AWS: everything else is API calls."""

from datetime import UTC, datetime, timedelta

import reaper
from reaper import State, decide

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def _state(age_hours: float, **kw: object) -> State:
    base: dict[str, object] = {
        "cluster_status": "ACTIVE",
        "created": NOW - timedelta(hours=age_hours),
        "node_groups": {"main": ("ACTIVE", 2)},
    }
    base.update(kw)
    return State(**base)  # type: ignore[arg-type]


def test_a_young_cluster_is_left_alone() -> None:
    assert decide(NOW, _state(1), 4, 6) == []


def test_after_four_hours_the_servers_are_scaled_to_zero() -> None:
    assert decide(NOW, _state(4.2), 4, 6) == [("scale_node_group_to_zero", "main")]


def test_the_scale_in_boundary_is_inclusive() -> None:
    assert decide(NOW, _state(4.0), 4, 6) != []
    assert decide(NOW, _state(3.99), 4, 6) == []


def test_servers_already_at_zero_are_not_scaled_again() -> None:
    state = _state(5, node_groups={"main": ("ACTIVE", 0)})
    assert decide(NOW, state, 4, 6) == []


def test_a_node_group_that_is_busy_changing_is_not_poked_while_deleting() -> None:
    state = _state(5, node_groups={"main": ("DELETING", 2)})
    assert decide(NOW, state, 4, 6) == []


def test_after_six_hours_load_balancers_go_first_then_the_node_group() -> None:
    state = _state(6.5, load_balancers=["arn:lb1"])
    assert decide(NOW, state, 4, 6) == [
        ("delete_load_balancer", "arn:lb1"),
        ("delete_node_group", "main"),
    ]


def test_the_cluster_is_deleted_only_once_no_node_group_is_left() -> None:
    assert ("delete_cluster", reaper.CLUSTER_NAME) not in decide(NOW, _state(7), 4, 6)
    gone = _state(7, node_groups={})
    assert decide(NOW, gone, 4, 6) == [("delete_cluster", reaper.CLUSTER_NAME)]


def test_a_cluster_already_being_deleted_is_not_deleted_again() -> None:
    assert decide(NOW, _state(7, node_groups={}, cluster_status="DELETING"), 4, 6) == []


def test_a_node_group_already_being_deleted_is_left_to_finish() -> None:
    state = _state(7, node_groups={"main": ("DELETING", 0)})
    assert decide(NOW, state, 4, 6) == []


def test_orphans_of_a_gone_cluster_are_cleaned_load_balancer_first() -> None:
    state = State(
        cluster_status=None,
        load_balancers=["arn:lb"],
        security_groups=["sg-1"],
        target_groups=["arn:tg"],
    )
    # The security group is still held by the load balancer: wait for it to go.
    assert decide(NOW, state, 4, 6) == [("delete_load_balancer", "arn:lb")]
    state.load_balancers = []
    assert decide(NOW, state, 4, 6) == [
        ("delete_target_group", "arn:tg"),
        ("delete_security_group", "sg-1"),
    ]


def test_a_gone_cluster_with_nothing_left_does_nothing() -> None:
    assert decide(NOW, State(cluster_status=None), 4, 6) == []


def test_a_future_creation_time_does_not_go_negative() -> None:
    # Clock skew between AWS and the function must not look like a very old or a negative age.
    assert decide(NOW, _state(-0.01), 4, 6) == []
