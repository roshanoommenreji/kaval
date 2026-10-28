"""The EKS lab's self-destruct timer (Phase 7, KAV-101, ADR-0067).

An EKS control plane costs $0.10 an hour whether or not anyone is using it, so the lab must not be
left running. This runs every 15 minutes (EventBridge Scheduler) and works only from the cluster's
age, which AWS records at creation, so nobody has to remember to start a clock:

  * after SCALE_IN_HOURS (default 4): scale every node group to zero. The servers stop billing; the
    cluster stays, so a person can still look.
  * after DELETE_HOURS (default 6): take it all down, in the only order AWS allows. Load balancers
    first (the controller made them, so Terraform does not know them and `terraform destroy` would
    leave them billing), then the node groups, then the cluster, then the loose security groups and
    target groups the controller left behind.

Each pass does the next step that is possible and returns; the next pass does the one after. A step
that fails (a security group still held by a load balancer that is being deleted) is retried.

It deletes outside Terraform, so afterwards `make lab-down` is still run: it finds the cluster gone,
drops it from the record, and removes the rest (network, roles).

It touches only what carries this cluster's name: the cluster itself, and load balancers, security
groups and target groups tagged `elbv2.k8s.aws/cluster=<name>` by the load balancer controller. IAM
enforces the same limit, so a bug here cannot reach anything else.

The decision is a pure function (`decide`) so it is unit-tested without AWS.
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger()
log.setLevel(logging.INFO)

CLUSTER_NAME = os.environ.get("CLUSTER_NAME", "")
SCALE_IN_HOURS = float(os.environ.get("SCALE_IN_HOURS", "4"))
DELETE_HOURS = float(os.environ.get("DELETE_HOURS", "6"))
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"

# What the load balancer controller puts on everything it makes for a cluster.
LBC_TAG = "elbv2.k8s.aws/cluster"


@dataclass
class State:
    """What exists right now, for the cluster this timer watches."""

    cluster_status: str | None  # None: the cluster does not exist
    created: datetime | None = None
    # name -> (status, desired size)
    node_groups: dict[str, tuple[str, int]] = field(default_factory=dict)
    load_balancers: list[str] = field(default_factory=list)  # arns
    security_groups: list[str] = field(default_factory=list)  # ids
    target_groups: list[str] = field(default_factory=list)  # arns


Action = tuple[str, str]


def decide(now: datetime, state: State, scale_in_hours: float, delete_hours: float) -> list[Action]:
    """The steps to take now, in order. Empty means do nothing."""
    actions: list[Action] = []

    if state.cluster_status is None:
        # The cluster is gone, so anything still tagged with its name is an orphan, of any age.
        actions += [("delete_load_balancer", arn) for arn in state.load_balancers]
        if not state.load_balancers:
            actions += [("delete_target_group", arn) for arn in state.target_groups]
            actions += [("delete_security_group", sg) for sg in state.security_groups]
        return actions

    if state.created is None:
        return []
    age_hours = max(0.0, (now - state.created).total_seconds() / 3600)

    if age_hours >= delete_hours:
        actions += [("delete_load_balancer", arn) for arn in state.load_balancers]
        for name, (status, _desired) in sorted(state.node_groups.items()):
            if status != "DELETING":
                actions.append(("delete_node_group", name))
        if not state.node_groups and state.cluster_status == "ACTIVE":
            actions.append(("delete_cluster", CLUSTER_NAME))
        return actions

    if age_hours >= scale_in_hours:
        for name, (status, desired) in sorted(state.node_groups.items()):
            if desired > 0 and status in ("ACTIVE", "UPDATING"):
                actions.append(("scale_node_group_to_zero", name))
    return actions


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    import boto3

    if not CLUSTER_NAME:
        raise RuntimeError("CLUSTER_NAME is not set")
    eks = boto3.client("eks")
    elb = boto3.client("elbv2")
    ec2 = boto3.client("ec2")

    state = _observe(eks, elb, ec2)
    actions = decide(datetime.now(UTC), state, SCALE_IN_HOURS, DELETE_HOURS)
    log.info(
        "cluster=%s status=%s created=%s actions=%s",
        CLUSTER_NAME, state.cluster_status, state.created, actions,
    )
    if not actions:
        return {"status": "no-op"}
    if DRY_RUN:
        log.warning("DRY_RUN: would do %s", actions)
        return {"status": "dry-run", "actions": actions}

    done: list[Action] = []
    for kind, target in actions:
        try:
            _do(kind, target, eks, elb, ec2)
            log.warning("LAB REAPER APPLIED: %s %s", kind, target)
            done.append((kind, target))
        except Exception:  # an AWS error; the next pass tries the step again
            # Expected while something is still being deleted; the next pass tries again.
            log.exception("Could not %s %s -- will retry on the next pass.", kind, target)
    return {"status": "acted", "actions": done}


def _observe(eks: Any, elb: Any, ec2: Any) -> State:
    try:
        cluster = eks.describe_cluster(name=CLUSTER_NAME)["cluster"]
    except eks.exceptions.ResourceNotFoundException:
        cluster = None

    state = State(cluster_status=cluster["status"] if cluster else None)
    if cluster:
        state.created = cluster["createdAt"]
        for name in eks.list_nodegroups(clusterName=CLUSTER_NAME)["nodegroups"]:
            group = eks.describe_nodegroup(clusterName=CLUSTER_NAME, nodegroupName=name)
            group = group["nodegroup"]
            state.node_groups[name] = (group["status"], group["scalingConfig"]["desiredSize"])

    # Load balancers and target groups: the API cannot filter by tag, so list and ask for the tags.
    for page in elb.get_paginator("describe_load_balancers").paginate():
        arns = [lb["LoadBalancerArn"] for lb in page["LoadBalancers"]]
        state.load_balancers += _tagged(elb, arns)
    for page in elb.get_paginator("describe_target_groups").paginate():
        arns = [tg["TargetGroupArn"] for tg in page["TargetGroups"]]
        state.target_groups += _tagged(elb, arns)
    for page in ec2.get_paginator("describe_security_groups").paginate(
        Filters=[{"Name": f"tag:{LBC_TAG}", "Values": [CLUSTER_NAME]}]
    ):
        state.security_groups += [sg["GroupId"] for sg in page["SecurityGroups"]]
    return state


def _tagged(elb: Any, arns: list[str]) -> list[str]:
    out: list[str] = []
    for i in range(0, len(arns), 20):  # describe_tags takes 20 at a time
        for desc in elb.describe_tags(ResourceArns=arns[i : i + 20])["TagDescriptions"]:
            if any(t["Key"] == LBC_TAG and t["Value"] == CLUSTER_NAME for t in desc["Tags"]):
                out.append(desc["ResourceArn"])
    return out


def _do(kind: str, target: str, eks: Any, elb: Any, ec2: Any) -> None:
    if kind == "scale_node_group_to_zero":
        eks.update_nodegroup_config(
            clusterName=CLUSTER_NAME,
            nodegroupName=target,
            scalingConfig={"minSize": 0, "desiredSize": 0},
        )
    elif kind == "delete_node_group":
        eks.delete_nodegroup(clusterName=CLUSTER_NAME, nodegroupName=target)
    elif kind == "delete_cluster":
        eks.delete_cluster(name=target)
    elif kind == "delete_load_balancer":
        elb.delete_load_balancer(LoadBalancerArn=target)
    elif kind == "delete_target_group":
        elb.delete_target_group(TargetGroupArn=target)
    elif kind == "delete_security_group":
        ec2.delete_security_group(GroupId=target)
    else:
        raise ValueError(f"unknown action {kind}")
