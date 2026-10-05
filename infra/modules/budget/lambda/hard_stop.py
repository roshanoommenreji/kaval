"""Budget hard stop.

Invoked by SNS when AWS Budgets reports actual month-to-date spend above the
hard-stop threshold. Two independent actions, each reported separately:

  1. Scale the Kaval Auto Scaling Group to zero (the Phase 4 k3s node).
  2. STOP every running EC2 instance tagged with the Kaval project tag that is
     not part of an Auto Scaling Group, e.g. the dev server (ADR-0007) and the
     database server (ADR-0008) -- snapshotting each one's data volume first
     (tagged Role=database-data), the same pre-stop snapshot `make down` takes.
     Stop, never terminate: the disk survives and `make devbox-up`/`make up`
     restores it. ASG members are skipped because the ASG would just replace
     them; action 1 handles those.

This is deliberately blunt. It is the last line of defence, not a cost
optimiser -- the FinOps agent (Phase 7) does the nuanced work. By the time
this runs, something has already gone wrong.

Notes:
  * The ASG action runs in DRY_RUN mode until explicitly disabled, and
    ASG_NAME is empty until Phase 4 creates the group.
  * The instance action is enabled by STOP_INSTANCES=true and has its own
    permission, scoped by IAM to instances carrying the project tag.
  * The snapshot step never blocks the stop: a snapshot failure is logged and
    swallowed, because stopping the bill is this function's one job.
  * Budgets data lags by hours, so this reacts in hours, not minutes.
"""

import logging
import os

import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)

ASG_NAME = os.environ.get("ASG_NAME", "")
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"
STOP_INSTANCES = os.environ.get("STOP_INSTANCES", "false").lower() == "true"
TAG_KEY = os.environ.get("STOP_TAG_KEY", "Project")
TAG_VALUE = os.environ.get("STOP_TAG_VALUE", "kaval")

# The only volume role this function snapshots before stopping its instance (ADR-0008).
# Not every Project=kaval instance has one -- the dev server doesn't -- so this is a
# lookup per instance, not an assumption that one exists.
DATA_VOLUME_ROLE_TAG = "database-data"


def handler(event, context):
    log.info(
        "Budget hard stop triggered. asg=%r asg_dry_run=%s stop_instances=%s tag=%s=%s",
        ASG_NAME, DRY_RUN, STOP_INSTANCES, TAG_KEY, TAG_VALUE,
    )
    for record in event.get("Records", []):
        log.info("SNS subject: %s", record.get("Sns", {}).get("Subject", ""))
        log.info("SNS message: %s", record.get("Sns", {}).get("Message", ""))

    return {"asg": _scale_asg_to_zero(), "instances": _stop_tagged_instances()}


def _scale_asg_to_zero():
    if not ASG_NAME:
        log.warning("ASG_NAME is empty -- no Auto Scaling Group to scale (expected before Phase 4).")
        return {"status": "no-op", "reason": "no asg configured"}

    if DRY_RUN:
        log.warning(
            "DRY_RUN is enabled. Would have set desired=0 min=0 on %s. "
            "Set hard_stop_dry_run = false in Terraform to arm this.",
            ASG_NAME,
        )
        return {"status": "dry-run", "asg": ASG_NAME}

    asg = boto3.client("autoscaling")
    groups = asg.describe_auto_scaling_groups(AutoScalingGroupNames=[ASG_NAME])
    if not groups["AutoScalingGroups"]:
        log.error("ASG %s not found. Nothing scaled down.", ASG_NAME)
        return {"status": "error", "reason": "asg not found", "asg": ASG_NAME}

    before = groups["AutoScalingGroups"][0]["DesiredCapacity"]
    asg.update_auto_scaling_group(AutoScalingGroupName=ASG_NAME, MinSize=0, DesiredCapacity=0)
    log.warning(
        "HARD STOP APPLIED. %s scaled %d -> 0. Storage preserved; restore with `make up`.",
        ASG_NAME, before,
    )
    return {"status": "stopped", "asg": ASG_NAME, "desired_before": before}


def _snapshot_data_volumes(ec2, instance_ids):
    """Snapshot each instance's database-data volume, if it has one. Best-effort:
    a failure here is logged and never stops the actual stop from happening."""
    snapshot_ids = []
    for instance_id in instance_ids:
        try:
            volumes = ec2.describe_volumes(
                Filters=[
                    {"Name": "attachment.instance-id", "Values": [instance_id]},
                    {"Name": "tag:Role", "Values": [DATA_VOLUME_ROLE_TAG]},
                ]
            )["Volumes"]
        except Exception:
            log.exception("Could not look up data volumes for %s -- stopping without a snapshot.", instance_id)
            continue

        for volume in volumes:
            try:
                snapshot = ec2.create_snapshot(
                    VolumeId=volume["VolumeId"],
                    Description=f"kaval hard-stop pre-stop snapshot of {instance_id}",
                    TagSpecifications=[{
                        "ResourceType": "snapshot",
                        "Tags": [
                            {"Key": "Name", "Value": "kaval-hard-stop-pre-stop"},
                            {"Key": "Reason", "Value": "pre-stop"},
                            {"Key": "Project", "Value": TAG_VALUE},
                            {"Key": "Role", "Value": DATA_VOLUME_ROLE_TAG},
                        ],
                    }],
                )
                log.warning(
                    "Snapshotted %s as %s before stopping %s.",
                    volume["VolumeId"], snapshot["SnapshotId"], instance_id,
                )
                snapshot_ids.append(snapshot["SnapshotId"])
            except Exception:
                log.exception("Snapshot of %s failed -- stopping %s anyway.", volume["VolumeId"], instance_id)

    return snapshot_ids


def _stop_tagged_instances():
    if not STOP_INSTANCES:
        return {"status": "disabled"}

    ec2 = boto3.client("ec2")
    ids = []
    for page in ec2.get_paginator("describe_instances").paginate(
        Filters=[
            {"Name": f"tag:{TAG_KEY}", "Values": [TAG_VALUE]},
            {"Name": "instance-state-name", "Values": ["running", "pending"]},
        ]
    ):
        for reservation in page["Reservations"]:
            for instance in reservation["Instances"]:
                tags = {t["Key"] for t in instance.get("Tags", [])}
                if "aws:autoscaling:groupName" in tags:
                    continue
                ids.append(instance["InstanceId"])

    if not ids:
        log.info("No running %s=%s instances outside an ASG. Nothing to stop.", TAG_KEY, TAG_VALUE)
        return {"status": "no-op", "reason": "nothing running"}

    snapshots = _snapshot_data_volumes(ec2, ids)

    ec2.stop_instances(InstanceIds=ids)
    log.warning(
        "HARD STOP APPLIED. Stopped %s. Disks preserved; restart with `make devbox-up`/`make up` "
        "once the cause is understood.",
        ", ".join(ids),
    )
    return {"status": "stopped", "instances": ids, "snapshots": snapshots}
