"""The spending brake, tested without AWS (KAV-96).

What matters most is what it must NOT do: nothing in dry-run, nothing to a server outside the
project tag, nothing to a group that is already at zero, and never letting one failing step stop
the others. Each test gives the function a fake AWS that records the calls it receives."""

from typing import Any

import hard_stop as h
import pytest


class FakeAutoScaling:
    def __init__(self, groups: dict[str, dict[str, int]], fail_on: str = "") -> None:
        self.groups = groups
        self.fail_on = fail_on
        self.updated: list[tuple[str, int, int]] = []

    def describe_auto_scaling_groups(self, AutoScalingGroupNames: list[str]) -> dict[str, Any]:
        found = [
            {"AutoScalingGroupName": n, "DesiredCapacity": g["desired"], "MinSize": g["min"]}
            for n, g in self.groups.items()
            if n in AutoScalingGroupNames
        ]
        return {"AutoScalingGroups": found}

    def update_auto_scaling_group(
        self, AutoScalingGroupName: str, MinSize: int, DesiredCapacity: int
    ) -> None:
        if AutoScalingGroupName == self.fail_on:
            raise RuntimeError("denied")
        self.updated.append((AutoScalingGroupName, MinSize, DesiredCapacity))


class FakeEc2:
    def __init__(self, instances: list[dict[str, Any]], snapshot_fails: bool = False) -> None:
        self.instances = instances
        self.snapshot_fails = snapshot_fails
        self.stopped: list[str] = []
        self.snapshots: list[str] = []

    def get_paginator(self, name: str) -> "FakeEc2":
        assert name == "describe_instances"
        return self

    def paginate(self, Filters: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{"Reservations": [{"Instances": self.instances}]}]

    def describe_volumes(self, Filters: list[dict[str, Any]]) -> dict[str, Any]:
        return {"Volumes": [{"VolumeId": "vol-1", "Tags": [{"Key": "Env", "Value": "staging"}]}]}

    def create_snapshot(self, **kw: Any) -> dict[str, str]:
        if self.snapshot_fails:
            raise RuntimeError("snapshot refused")
        self.snapshots.append(kw["VolumeId"])
        return {"SnapshotId": "snap-1"}

    def stop_instances(self, InstanceIds: list[str]) -> None:
        self.stopped += InstanceIds


def instance(iid: str, *, in_group: bool = False) -> dict[str, Any]:
    tags = [{"Key": "Project", "Value": "kaval"}]
    if in_group:
        tags.append({"Key": "aws:autoscaling:groupName", "Value": "kaval-prod"})
    return {"InstanceId": iid, "Tags": tags}


def arm(
    monkeypatch: pytest.MonkeyPatch,
    asg: FakeAutoScaling,
    ec2: FakeEc2,
    *,
    names: list[str],
    dry_run: bool = False,
    stop_instances: bool = True,
) -> None:
    monkeypatch.setattr(h, "ASG_NAMES", names)
    monkeypatch.setattr(h, "DRY_RUN", dry_run)
    monkeypatch.setattr(h, "STOP_INSTANCES", stop_instances)
    monkeypatch.setattr(h.boto3, "client", lambda svc: asg if svc == "autoscaling" else ec2)


def groups(**desired: int) -> dict[str, dict[str, int]]:
    return {name: {"desired": n, "min": n} for name, n in desired.items()}


def test_the_names_are_a_comma_list_and_blanks_are_dropped() -> None:
    assert h._names("kaval-prod, kaval-staging ,,") == ["kaval-prod", "kaval-staging"]
    assert h._names("") == []


def test_every_named_group_is_scaled_to_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    asg, ec2 = FakeAutoScaling(groups(**{"kaval-prod": 1, "kaval-staging": 1})), FakeEc2([])
    arm(monkeypatch, asg, ec2, names=["kaval-prod", "kaval-staging"])
    result = h.handler({}, None)
    assert asg.updated == [("kaval-prod", 0, 0), ("kaval-staging", 0, 0)]
    assert [r["status"] for r in result["asgs"]] == ["stopped", "stopped"]


def test_dry_run_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    asg, ec2 = FakeAutoScaling(groups(**{"kaval-prod": 1})), FakeEc2([])
    arm(monkeypatch, asg, ec2, names=["kaval-prod"], dry_run=True)
    assert h.handler({}, None)["asgs"] == [{"asg": "kaval-prod", "status": "dry-run"}]
    assert asg.updated == []


def test_a_group_already_at_zero_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    asg, ec2 = FakeAutoScaling(groups(**{"kaval-prod": 0})), FakeEc2([])
    arm(monkeypatch, asg, ec2, names=["kaval-prod"])
    assert h.handler({}, None)["asgs"][0]["status"] == "already-zero"
    assert asg.updated == []


def test_a_group_that_does_not_exist_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # Staging is destroyed between uses: the nightly run must not fail every night.
    asg, ec2 = FakeAutoScaling(groups(**{"kaval-prod": 1})), FakeEc2([])
    arm(monkeypatch, asg, ec2, names=["kaval-prod", "kaval-staging"])
    result = h.handler({}, None)
    assert [r["status"] for r in result["asgs"]] == ["stopped", "absent"]


def test_no_names_means_nothing_to_scale(monkeypatch: pytest.MonkeyPatch) -> None:
    asg, ec2 = FakeAutoScaling({}), FakeEc2([])
    arm(monkeypatch, asg, ec2, names=[])
    assert h.handler({}, None)["asgs"] == []


def test_one_group_failing_does_not_stop_the_others_or_the_servers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asg = FakeAutoScaling(groups(**{"kaval-prod": 1, "kaval-staging": 1}), fail_on="kaval-prod")
    ec2 = FakeEc2([instance("i-db")])
    arm(monkeypatch, asg, ec2, names=["kaval-prod", "kaval-staging"])
    with pytest.raises(RuntimeError, match="incomplete"):
        h.handler({}, None)
    assert asg.updated == [("kaval-staging", 0, 0)]  # the second group still got its stop
    assert ec2.stopped == ["i-db"]  # and so did the standalone server


def test_a_failure_in_the_server_step_still_lets_the_groups_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asg, ec2 = FakeAutoScaling(groups(**{"kaval-prod": 1})), FakeEc2([instance("i-db")])
    arm(monkeypatch, asg, ec2, names=["kaval-prod"])

    def broken() -> None:
        raise RuntimeError("ec2 down")

    monkeypatch.setattr(h, "_stop_tagged_instances", broken)
    with pytest.raises(RuntimeError, match="incomplete"):
        h.handler({}, None)
    assert asg.updated == [("kaval-prod", 0, 0)]


def test_servers_inside_a_group_are_skipped_because_the_group_replaces_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asg = FakeAutoScaling({})
    ec2 = FakeEc2([instance("i-db"), instance("i-node", in_group=True)])
    arm(monkeypatch, asg, ec2, names=[])
    h.handler({}, None)
    assert ec2.stopped == ["i-db"]


def test_the_data_disk_is_copied_before_the_server_is_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asg, ec2 = FakeAutoScaling({}), FakeEc2([instance("i-db")])
    arm(monkeypatch, asg, ec2, names=[])
    result = h.handler({}, None)
    assert ec2.snapshots == ["vol-1"] and ec2.stopped == ["i-db"]
    assert result["instances"]["snapshots"] == ["snap-1"]


def test_a_failed_copy_never_blocks_the_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    asg, ec2 = FakeAutoScaling({}), FakeEc2([instance("i-db")], snapshot_fails=True)
    arm(monkeypatch, asg, ec2, names=[])
    h.handler({}, None)
    assert ec2.stopped == ["i-db"]


def test_servers_are_left_alone_when_that_action_is_switched_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asg, ec2 = FakeAutoScaling(groups(**{"kaval-prod": 1})), FakeEc2([instance("i-db")])
    arm(monkeypatch, asg, ec2, names=["kaval-prod"], stop_instances=False)
    result = h.handler({}, None)
    assert ec2.stopped == [] and result["instances"] == {"status": "disabled"}
