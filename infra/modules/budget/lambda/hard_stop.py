"""Budget hard stop.

Invoked by SNS when AWS Budgets reports actual month-to-date spend above the
hard-stop threshold. Scales the Kaval Auto Scaling Group to zero, which
terminates the k3s node and stops essentially all recurring cost.

This is deliberately blunt. It is the last line of defence, not a cost
optimiser -- the FinOps agent (Phase 7) does the nuanced work. By the time
this runs, something has already gone wrong.

Notes:
  * Runs in DRY_RUN mode until explicitly disabled. Watch it fire once and
    read the log before trusting it.
  * ASG_NAME is empty during Phase 0 because no compute exists yet. The
    function is still deployed and still fires -- proving the wiring works
    before there is anything to protect.
  * Storage (EBS, ECR, S3) survives. Restore with `make up`.
"""

import logging
import os

import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)

ASG_NAME = os.environ.get("ASG_NAME", "")
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"


def handler(event, context):
    log.info("Budget hard stop triggered. dry_run=%s asg=%r", DRY_RUN, ASG_NAME)

    for record in event.get("Records", []):
        subject = record.get("Sns", {}).get("Subject", "")
        message = record.get("Sns", {}).get("Message", "")
        log.info("SNS subject: %s", subject)
        log.info("SNS message: %s", message)

    if not ASG_NAME:
        log.warning(
            "ASG_NAME is empty -- nothing to scale. This is expected in Phase 0: "
            "the alarm path is being proven before any compute exists."
        )
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

    asg.update_auto_scaling_group(
        AutoScalingGroupName=ASG_NAME,
        MinSize=0,
        DesiredCapacity=0,
    )

    log.warning(
        "HARD STOP APPLIED. %s scaled %d -> 0. Cluster is down. "
        "Storage preserved; restore with `make up` once the cause is understood.",
        ASG_NAME,
        before,
    )

    return {"status": "stopped", "asg": ASG_NAME, "desired_before": before}
