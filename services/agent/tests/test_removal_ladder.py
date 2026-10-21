"""The whole removal ladder, end to end, with a pretend AWS (KAV-95, ADR-0063).

Every real piece runs: the scan, the reader that turns it into signals, the correlator, the planner,
the approval, the executor, the removal worker, the reader of the worker's answer. Only AWS (a small
world of disks and backup copies) and the cluster's one ConfigMap are pretend, and the clock is
moved by hand. It is the "real dependency" test between the unit tests and the live proof, and it
holds the negative cases that matter most: a disk that gets attached during the wait is never
deleted, and a delete order nobody should have sent is refused by the worker.

Needs Postgres (skips, not fails, when none is reachable)."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from kaval_agent import correlate
from kaval_agent import policy as policy_mod
from kaval_agent import removal as planner
from kaval_collector import removal as answers_reader
from kaval_collector import waste as waste_reader
from kaval_executor import executor
from kaval_gateway.decisions import record_decision
from kaval_shared.models import (
    Action,
    ExecutionStatus,
    Incident,
    PolicyClass,
    Signal,
    Verdict,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[3]
VID = "vol-0abc1234def56789a"
# The pretend clock starts at the real "now": the gateway refuses an approval on a proposal older than a
# day by the REAL clock, so a date written into the test would fail the day after it was written
# (found 2026-10-21). Every step below is measured from this, so nothing else depends on the date.
D1 = datetime.now(UTC).replace(minute=15, second=0, microsecond=0)
GRACE = 1.0
SETTINGS = planner.Settings(enabled=True, grace_hours=GRACE)


def _load(name: str, *parts: str) -> Any:
    path = ROOT.joinpath(*parts)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


scan = _load("ladder_waste_scan", "infra", "modules", "waste-scan", "lambda", "waste_scan.py")
worker = _load(
    "ladder_waste_removal", "infra", "modules", "waste-removal", "lambda", "waste_removal.py"
)


class World:
    """A tiny AWS: disks and backup copies, changed by the worker's four calls."""

    def __init__(self, now: datetime) -> None:
        self.now = now
        self.volumes: list[dict[str, Any]] = [
            {
                "VolumeId": VID,
                "State": "available",
                "Size": 20,
                "VolumeType": "gp3",
                "Attachments": [],
                "Tags": [
                    {"Key": "Project", "Value": "kaval"},
                    {"Key": "Env", "Value": "staging"},
                    {"Key": "ManagedBy", "Value": "manual"},
                ],
            }
        ]
        self.snapshots: list[dict[str, Any]] = []
        self.calls: list[str] = []

    def inventory(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "instances": [],
            "addresses": [],
            "volumes": [dict(v) for v in self.volumes],
            "snapshots": [dict(s) for s in self.snapshots],
        }

    def attach(self, vid: str) -> None:
        for v in self.volumes:
            if v["VolumeId"] == vid:
                v["State"], v["Attachments"] = "in-use", [{"InstanceId": "i-0abc"}]


class FakeEc2:
    def __init__(self, world: World) -> None:
        self.w = world
        self._name = ""

    def get_paginator(self, name: str) -> FakeEc2:
        self._name = name
        return self

    def paginate(self, **_: Any) -> list[dict[str, Any]]:
        inv = self.w.inventory()
        return [
            {"Volumes": inv["volumes"]}
            if self._name == "describe_volumes"
            else {"Snapshots": inv["snapshots"]}
        ]

    def create_snapshot(
        self,
        VolumeId: str,
        TagSpecifications: list[dict[str, Any]],  # noqa: N803
        **_: Any,
    ) -> dict[str, Any]:
        self.w.calls.append("create_snapshot")
        sid = f"snap-0{len(self.w.snapshots):016x}"
        self.w.snapshots.append(
            {
                "SnapshotId": sid,
                "VolumeId": VolumeId,
                "State": "completed",
                "VolumeSize": 20,
                "Description": "",
                "StartTime": self.w.now,
                "Tags": TagSpecifications[0]["Tags"],
            }
        )
        return {"SnapshotId": sid}

    def create_tags(self, Resources: list[str], Tags: list[dict[str, str]]) -> None:  # noqa: N803
        self.w.calls.append("create_tags")
        for v in self.w.volumes:
            if v["VolumeId"] in Resources:
                v["Tags"] = [t for t in v["Tags"] if t["Key"] != Tags[0]["Key"]] + Tags

    def delete_volume(self, VolumeId: str) -> None:  # noqa: N803
        self.w.calls.append("delete_volume")
        self.w.volumes = [v for v in self.w.volumes if v["VolumeId"] != VolumeId]

    def delete_snapshot(self, SnapshotId: str) -> None:  # noqa: N803
        self.w.calls.append("delete_snapshot")


class FakeSsm:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def get_parameter(self, Name: str) -> dict[str, Any]:  # noqa: N803
        if Name not in self.store:
            raise KeyError(Name)
        return {"Parameter": {"Value": self.store[Name]}}

    def put_parameter(self, Name: str, Value: str, **_: Any) -> None:  # noqa: N803
        self.store[Name] = Value


class FakeCluster:
    """The orders ConfigMap, and the result ConfigMap the server's timer fills."""

    def __init__(self) -> None:
        self.data: dict[str, dict[str, str]] = {
            "orders": {"orders.json": '{"orders":[]}'},
            "result": {},
        }

    def read_namespaced_config_map(self, name: str, namespace: str) -> Any:
        key = "orders" if name.endswith("orders") else "result"
        return type("CM", (), {"data": self.data[key]})()

    def patch_namespaced_config_map(self, name: str, namespace: str, body: dict[str, Any]) -> None:
        self.data["orders"].update(body["data"])


class Harness:
    """Everything wired together, with one clock."""

    def __init__(self, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
        self.db, self.mp = db, monkeypatch
        self.now = D1
        self.world = World(D1)
        self.cluster = FakeCluster()
        self.ssm = FakeSsm()
        self.previous: dict[str, str] = {}
        monkeypatch.setenv("KAVAL_REMOVAL_ENABLED", "true")
        monkeypatch.setenv("KAVAL_NAMESPACE", "kaval-staging")
        monkeypatch.setenv("KAVAL_REMOVAL_ORDERS", "kaval-staging-removal-orders")
        monkeypatch.setattr(policy_mod, "classify", lambda *a, **kw: PolicyClass.ask)
        monkeypatch.setattr(executor, "_core_v1", lambda: self.cluster)
        ec2, ssm = FakeEc2(self.world), self.ssm
        monkeypatch.setitem(
            sys.modules,
            "boto3",
            type("B", (), {"client": staticmethod(lambda n: ec2 if n == "ec2" else ssm)}),
        )
        for name, value in {
            "ORDERS_PARAMETER": "/o",
            "RESULT_PARAMETER": "/r",
            "ENV_NAME": "staging",
            "GRACE_HOURS": GRACE,
            "ENABLED": True,
        }.items():
            monkeypatch.setattr(worker, name, value)
        clock = self

        class Frozen(datetime):
            @classmethod
            def now(cls, tz: Any = None) -> Any:
                return clock.now

        monkeypatch.setattr(worker, "datetime", Frozen)

    def at(self, hours: float) -> None:
        self.now = D1 + timedelta(hours=hours)
        self.world.now = self.now

    def run_scan(self) -> dict[str, Any]:
        doc: dict[str, Any] = scan.scan(self.world.inventory(), self.now, "staging", self.previous)
        self.previous = doc["candidates"]
        handoff = waste_reader.parse_handoff({"scan.json": scan.encode(doc)})
        waste_reader.run_pass(self.db, _OneShot(handoff), now=self.now + timedelta(minutes=5))
        return doc

    def correlate(self) -> None:
        correlate.correlate(self.db, now=self.now + timedelta(minutes=30))

    def plan(self) -> list[str]:
        return planner.plan_pass(self.db, SETTINGS, now=self.now + timedelta(minutes=40))

    def approve_and_execute(self) -> Action:
        action = self.db.scalars(
            select(Action).where(~Action.decision.has()).order_by(Action.created_at.desc())
        ).first()
        assert action is not None, "nothing was waiting for an approval"
        record_decision(self.db, action.id, Verdict.approved, "roshan")
        self.db.commit()
        # the approval is stamped by the real clock, so the executor judges its age by it too
        result = executor.execute_one(self.db, action)
        assert result.status == ExecutionStatus.success, result.detail
        assert "handed over" in result.detail or (
            action.execution is not None and "handed over" in (action.execution.stdout or "")
        )
        return action

    def carry_orders_to_the_worker_and_the_answer_back(self) -> None:
        """What the server's timer does: orders out to the parameter, the worker runs, the answer
        comes back into the result ConfigMap, the cost reader's pod reads it."""
        self.ssm.store["/o"] = self.cluster.data["orders"]["orders.json"]
        worker.handler({}, None)
        self.cluster.data["result"] = {"result.json": self.ssm.store["/r"]}
        orders = answers_reader.parse(self.cluster.data["orders"], "orders.json", "orders")
        results = answers_reader.parse(self.cluster.data["result"], "result.json", "results")
        answers_reader.run_pass(
            self.db, _Answers(results, orders), now=self.now + timedelta(minutes=50)
        )


class _OneShot:
    def __init__(self, handoff: Any) -> None:
        self._h = handoff

    def read(self) -> Any:
        return self._h


class _Answers:
    def __init__(self, answers: object, orders: object) -> None:
        self._h = answers_reader.Handoff(answers=answers, orders=orders)

    def read(self) -> Any:
        return self._h


def _tags(world: World) -> dict[str, str]:
    return {t["Key"]: t["Value"] for t in world.volumes[0]["Tags"]}


# ── the ladder ──────────────────────────────────────────────────────────────────────────────────


def test_a_loose_disk_goes_up_the_ladder_and_is_deleted_with_its_backup_kept(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(db_session, monkeypatch)
    h.run_scan()  # day 0: only remembered
    h.at(24)
    h.run_scan()
    h.correlate()
    assert h.plan(), "step 1 should be proposed for a loose disk seen twice"
    step1 = h.db.scalars(select(Action)).one()
    assert step1.type == planner.STEP_BACKUP and step1.params["preview"]

    h.approve_and_execute()  # first approval
    assert h.world.volumes[0]["VolumeId"] == VID  # nothing is deleted yet
    h.carry_orders_to_the_worker_and_the_answer_back()
    assert h.world.calls == ["create_snapshot", "create_tags"]  # backup first, then the mark
    assert w_mark(h.world) and len(h.world.snapshots) == 1

    # inside the grace period nothing more is asked
    h.at(24.1)
    h.run_scan()
    h.correlate()
    assert h.plan() == []

    # after the grace, the scan reports the mark and the finished backup; step 2 is proposed
    h.at(27)
    doc = h.run_scan()
    [finding] = [f for f in doc["findings"] if f["kind"] == "volume_unattached"]
    assert (
        finding["value"]["backup_state"] == "completed"
        and "pending_removal_since" in finding["value"]
    )
    h.correlate()
    assert len(h.plan()) == 1
    step2 = h.db.scalars(select(Action).where(Action.type == planner.STEP_DELETE)).first()
    assert step2 is not None and step2.type == planner.STEP_DELETE
    assert h.world.snapshots[0]["SnapshotId"] in str(step2.params["undo"])  # the way back is named

    h.approve_and_execute()  # second approval
    h.carry_orders_to_the_worker_and_the_answer_back()
    assert h.world.volumes == [] and h.world.calls[-1] == "delete_volume"
    assert len(h.world.snapshots) == 1  # the backup copy is kept

    # the next scan is clean and asks for nothing more
    h.at(30)
    assert h.run_scan()["findings"] == []
    h.correlate()
    assert h.plan() == []

    done = h.db.scalars(select(Signal).where(Signal.kind == "removal_done")).all()
    assert (
        len(done) == 2
        and not h.db.scalars(select(Signal).where(Signal.kind == "removal_failed")).all()
    )


def w_mark(world: World) -> bool:
    return worker.MARK_TAG in _tags(world)


def test_a_disk_attached_during_the_wait_is_never_deleted(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(db_session, monkeypatch)
    h.run_scan()
    h.at(24)
    h.run_scan()
    h.correlate()
    h.plan()
    h.approve_and_execute()
    h.carry_orders_to_the_worker_and_the_answer_back()

    h.world.attach(VID)  # someone starts using it
    h.at(27)
    assert not [f for f in h.run_scan()["findings"] if f["kind"] == "volume_unattached"]
    h.correlate()
    assert h.plan() == []  # the planner does not ask: the scan no longer sees it loose
    assert h.db.scalars(select(Action).where(Action.type == planner.STEP_DELETE)).all() == []


def test_a_delete_order_nobody_should_have_sent_is_refused_by_the_worker_itself(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even a forged, valid-looking order is checked against AWS: here the disk is attached."""
    h = Harness(db_session, monkeypatch)
    h.world.attach(VID)
    forged = {"orders": [{"id": "0f0f0f0f-aaaa-bbbb", "step": "delete_volume", "target": VID}]}
    h.ssm.store["/o"] = json.dumps(forged)
    worker.handler({}, None)
    [result] = json.loads(h.ssm.store["/r"])["results"]
    assert (result["status"], result["reason"]) == ("refused", "attached")
    assert h.world.calls == [] and len(h.world.volumes) == 1


def test_a_delete_order_for_an_unmarked_disk_is_refused_by_the_worker(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(db_session, monkeypatch)
    forged = {"orders": [{"id": "0f0f0f0f-cccc-dddd", "step": "delete_volume", "target": VID}]}
    h.ssm.store["/o"] = json.dumps(forged)
    worker.handler({}, None)
    [result] = json.loads(h.ssm.store["/r"])["results"]
    assert (result["status"], result["reason"]) == ("refused", "not_marked")
    assert h.world.calls == []


def test_a_refused_step_becomes_an_incident_for_a_person(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(db_session, monkeypatch)
    h.world.attach(VID)
    forged = {
        "orders": [
            {
                "id": "0f0f0f0f-eeee-ffff",
                "step": "delete_volume",
                "target": VID,
                "at": "2026-10-19T03:00:00Z",
            }
        ]
    }
    h.cluster.data["orders"] = {"orders.json": json.dumps(forged)}
    h.carry_orders_to_the_worker_and_the_answer_back()
    h.correlate()
    prints = [i.fingerprint for i in h.db.scalars(select(Incident))]
    assert f"removal_failed:aws:ebs/{VID}" in prints
