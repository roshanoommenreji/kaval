"""Staging idle self-stop (ADR-0029).

Runs every 15 minutes (EventBridge Scheduler). If nobody has touched staging for IDLE_SECONDS,
it parks it: scales the node's Auto Scaling Group to zero and stops the database server after
a pre-stop snapshot of its data volume. Nothing is destroyed; `make staging-up` resumes it and
`make staging-down` destroys it.

"Touched" is what this function can see from outside the instances, so the node needs no extra
permissions and the agent's no-write-access rule is untouched:

  * an open Session Manager session (a shell, or a port-forward for kubectl) on a staging server
  * a session that ended recently
  * a Run Command sent to a staging server recently (how scripts/ops/staging.sh and the lab
    checks talk to it)
  * the server having just been started: launch time is the floor, so a fresh node is never
    reaped before its first use. EC2 resets LaunchTime on every stop/start.

The decision is a pure function (`decide`) so it is unit-tested without AWS. Stopping the bill
is the one job here, so a failed snapshot is logged and never blocks the stop, same as the
budget hard stop (infra/modules/budget/lambda/hard_stop.py).
"""

import logging
import os
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

log = logging.getLogger()
log.setLevel(logging.INFO)

ASG_NAME = os.environ.get("ASG_NAME", "")
DATABASE_NAME = os.environ.get("DATABASE_NAME", "")  # the database server's Name tag
IDLE_SECONDS = int(float(os.environ.get("IDLE_HOURS", "4")) * 3600)
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"

# Same role tag pause-database.sh and the budget hard stop snapshot (ADR-0008).
DATA_VOLUME_ROLE_TAG = "database-data"


def decide(
    now: datetime,
    launch_times: Iterable[datetime],
    activity_times: Iterable[datetime],
    idle_seconds: int,
) -> dict[str, Any]:
    """Is staging idle? Pure: no AWS, no clock. `launch_times` is the floor on activity."""
    events = [*launch_times, *activity_times]
    if not events:
        return {"idle": False, "reason": "nothing running", "idle_seconds": 0}
    last = max(events)
    idle_for = max(0, int((now - last).total_seconds()))
    return {
        "idle": idle_for >= idle_seconds,
        "reason": f"last activity {last.isoformat()}",
        "idle_seconds": idle_for,
        "last_activity": last,
    }


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    import boto3

    now = datetime.now(UTC)
    asg = boto3.client("autoscaling")
    ec2 = boto3.client("ec2")
    ssm = boto3.client("ssm")

    node_ids = _node_instance_ids(asg)
    db_ids = _database_instance_ids(ec2)
    instance_ids = node_ids + db_ids
    if not instance_ids:
        log.info("Nothing running (asg=%s database=%s).", ASG_NAME, DATABASE_NAME)
        return {"status": "no-op", "reason": "nothing running"}

    launches = _launch_times(ec2, instance_ids)
    activity = _ssm_activity(ssm, instance_ids, now - timedelta(seconds=IDLE_SECONDS + 3600), now)
    verdict = decide(now, launches, activity, IDLE_SECONDS)
    log.info(
        "idle=%s idle_for=%ss limit=%ss %s (instances=%s)",
        verdict["idle"], verdict["idle_seconds"], IDLE_SECONDS, verdict["reason"], instance_ids,
    )
    if not verdict["idle"]:
        return {"status": "active", "idle_seconds": verdict["idle_seconds"]}

    if DRY_RUN:
        log.warning("DRY_RUN: would scale %s to 0 and stop %s.", ASG_NAME, db_ids)
        return {"status": "dry-run", "idle_seconds": verdict["idle_seconds"]}

    scaled = _scale_asg_to_zero(asg)
    snapshots = _snapshot_data_volumes(ec2, db_ids)
    if db_ids:
        ec2.stop_instances(InstanceIds=db_ids)
        log.warning("IDLE STOP APPLIED. Stopped %s; `make staging-up` resumes.", ", ".join(db_ids))
    return {
        "status": "stopped",
        "idle_seconds": verdict["idle_seconds"],
        "asg": scaled,
        "database": db_ids,
        "snapshots": snapshots,
    }


def _node_instance_ids(asg: Any) -> list[str]:
    if not ASG_NAME:
        return []
    groups = asg.describe_auto_scaling_groups(AutoScalingGroupNames=[ASG_NAME])
    groups = groups["AutoScalingGroups"]
    if not groups:
        return []
    return [
        i["InstanceId"]
        for i in groups[0]["Instances"]
        if i["LifecycleState"] in ("Pending", "InService")
    ]


def _database_instance_ids(ec2: Any) -> list[str]:
    if not DATABASE_NAME:
        return []
    ids: list[str] = []
    for page in ec2.get_paginator("describe_instances").paginate(
        Filters=[
            {"Name": "tag:Name", "Values": [DATABASE_NAME]},
            {"Name": "instance-state-name", "Values": ["running", "pending"]},
        ]
    ):
        for reservation in page["Reservations"]:
            ids.extend(i["InstanceId"] for i in reservation["Instances"])
    return ids


def _launch_times(ec2: Any, instance_ids: list[str]) -> list[datetime]:
    out: list[datetime] = []
    for reservation in ec2.describe_instances(InstanceIds=instance_ids)["Reservations"]:
        out.extend(i["LaunchTime"] for i in reservation["Instances"])
    return out


def _ssm_activity(
    ssm: Any, instance_ids: list[str], since: datetime, now: datetime
) -> list[datetime]:
    """Every session or command touching `instance_ids` since `since`. An open session counts as
    activity right now."""
    wanted = set(instance_ids)
    after = [{"key": "InvokedAfter", "value": since.strftime("%Y-%m-%dT%H:%M:%SZ")}]
    times: list[datetime] = []

    for state in ("Active", "History"):
        kwargs: dict[str, Any] = {"State": state}
        if state == "History":
            kwargs["Filters"] = after
        for page in ssm.get_paginator("describe_sessions").paginate(**kwargs):
            for session in page["Sessions"]:
                if session["Target"] not in wanted:
                    continue
                if state == "Active":
                    times.append(now)
                else:
                    times.append(session.get("EndDate") or session["StartDate"])

    for instance_id in instance_ids:
        for page in ssm.get_paginator("list_commands").paginate(
            InstanceId=instance_id, Filters=after
        ):
            times.extend(c["RequestedDateTime"] for c in page["Commands"])
    return times


def _scale_asg_to_zero(asg: Any) -> dict[str, Any]:
    if not ASG_NAME:
        return {"status": "no-op", "reason": "no asg configured"}
    groups = asg.describe_auto_scaling_groups(AutoScalingGroupNames=[ASG_NAME])["AutoScalingGroups"]
    if not groups:
        log.error("ASG %s not found. Nothing scaled down.", ASG_NAME)
        return {"status": "error", "reason": "asg not found"}
    before = groups[0]["DesiredCapacity"]
    asg.update_auto_scaling_group(AutoScalingGroupName=ASG_NAME, MinSize=0, DesiredCapacity=0)
    log.warning("IDLE STOP APPLIED. %s scaled %d -> 0.", ASG_NAME, before)
    return {"status": "stopped", "desired_before": before}


def _snapshot_data_volumes(ec2: Any, instance_ids: list[str]) -> list[str]:
    """Best-effort pre-stop snapshot of each database-data volume (ADR-0008)."""
    snapshot_ids: list[str] = []
    for instance_id in instance_ids:
        try:
            volumes = ec2.describe_volumes(
                Filters=[
                    {"Name": "attachment.instance-id", "Values": [instance_id]},
                    {"Name": "tag:Role", "Values": [DATA_VOLUME_ROLE_TAG]},
                ]
            )["Volumes"]
        except Exception:
            log.exception("No volume lookup for %s -- stopping without a snapshot.", instance_id)
            continue
        for volume in volumes:
            try:
                snapshot = ec2.create_snapshot(
                    VolumeId=volume["VolumeId"],
                    Description=f"kaval staging idle-stop pre-stop snapshot of {instance_id}",
                    TagSpecifications=[{
                        "ResourceType": "snapshot",
                        "Tags": [
                            {"Key": "Name", "Value": f"{DATABASE_NAME}-pre-stop"},
                            {"Key": "Reason", "Value": "pre-stop"},
                            {"Key": "Project", "Value": "kaval"},
                            {"Key": "Env", "Value": "staging"},
                            {"Key": "Role", "Value": DATA_VOLUME_ROLE_TAG},
                        ],
                    }],
                )
                snapshot_ids.append(snapshot["SnapshotId"])
            except Exception:
                log.exception("Snapshot of %s failed -- stopping anyway.", volume["VolumeId"])
    return snapshot_ids
