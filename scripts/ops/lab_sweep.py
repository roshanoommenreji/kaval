"""Is anything of the EKS lab still in the account? A sweep by label, not by memory. (ADR-0067)

Read-only. Run by hand with `make lab-sweep` after `make lab-down`, and again 24 hours later (the
Phase 7 exit gate: "teardown leaves zero billable resources"). Exit 0 only if nothing is left.

`terraform destroy` removes what Terraform made. It does not remove what the cluster made itself:
the load balancer controller makes load balancers, the disk driver makes disks, and neither is in
Terraform's record. Those are the resources that quietly bill after a "clean" destroy, so this looks
for them by the labels they carry (Env=lab-eks, or the controller's cluster label), not by asking
Terraform what it remembers.

Two kinds of finding:
  BILLING  costs money while it exists: the cluster, servers, a load balancer, a disk, a snapshot,
           an address, a NAT gateway, a network interface.
  LEFTOVER free on its own but proves the teardown was incomplete: network, roles, timer.

Account numbers are masked in the output. Another project shares the account; only resources that
carry the lab's own labels or name are looked at.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any

REGION = "ap-south-1"
NAME = "kaval-lab-eks"
ENV = "lab-eks"
LBC_TAG = "elbv2.k8s.aws/cluster"

# Tagged resource kinds (service/resource-type in the ARN) that cost money while they exist.
BILLING_KINDS = {
    "eks/cluster",
    "eks/nodegroup",
    "ec2/instance",
    "ec2/volume",
    "ec2/snapshot",
    "ec2/elastic-ip",
    "ec2/natgateway",
    "ec2/network-interface",
    "elasticloadbalancing/loadbalancer",
}


@dataclass
class Finding:
    billing: bool
    kind: str
    ident: str


@dataclass
class Snapshot:
    """What was found, as plain lists, so the verdict is a pure function."""

    tagged: list[tuple[str, str]] = field(default_factory=list)  # (kind, id) by the Env label
    clusters: list[str] = field(default_factory=list)
    instances: list[str] = field(default_factory=list)  # not terminated
    volumes: list[str] = field(default_factory=list)
    load_balancers: list[str] = field(default_factory=list)  # arns carrying the controller's label
    network_interfaces: list[str] = field(default_factory=list)  # in the lab's network
    vpcs: list[str] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    parameters: list[str] = field(default_factory=list)


def verdict(snap: Snapshot) -> list[Finding]:
    found: dict[tuple[str, str], Finding] = {}

    def add(billing: bool, kind: str, ident: str) -> None:
        found.setdefault((kind, ident), Finding(billing, kind, ident))

    for kind, ident in snap.tagged:
        add(kind in BILLING_KINDS, kind, ident)
    for ident in snap.clusters:
        add(True, "eks/cluster", ident)
    for ident in snap.instances:
        add(True, "ec2/instance", ident)
    for ident in snap.volumes:
        add(True, "ec2/volume", ident)
    for ident in snap.load_balancers:
        add(True, "elasticloadbalancing/loadbalancer", ident)
    for ident in snap.network_interfaces:
        add(True, "ec2/network-interface", ident)
    for ident in snap.vpcs:
        add(False, "ec2/vpc", ident)
    for ident in snap.roles:
        add(False, "iam/role", ident)
    for ident in snap.functions:
        add(False, "lambda/function", ident)
    for ident in snap.parameters:
        add(False, "ssm/parameter", ident)
    return sorted(found.values(), key=lambda f: (not f.billing, f.kind, f.ident))


def render(findings: list[Finding]) -> str:
    if not findings:
        return "lab sweep: nothing left. No billable resource, no leftover."
    billing = [f for f in findings if f.billing]
    lines = [
        f"lab sweep: {len(billing)} BILLING and {len(findings) - len(billing)} leftover "
        f"resource(s) still carry the lab's labels or name."
    ]
    for f in findings:
        lines.append(f"  {'BILLING ' if f.billing else 'leftover'}  {f.kind:<36} {f.ident}")
    return "\n".join(lines)


def exit_code(findings: list[Finding], billing_only: bool) -> int:
    if billing_only:
        return 1 if any(f.billing for f in findings) else 0
    return 1 if findings else 0


# ── fetching (the only part that talks to AWS) ───────────────────────────────────────────────────


def _mask(text: str) -> str:
    return re.sub(r"\d{12}", "<acct>", text)


def _aws(*args: str) -> Any:
    done = subprocess.run(
        ["aws", "--region", REGION, *args, "--output", "json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if done.returncode != 0:
        raise SystemExit(f"aws {' '.join(args[:2])} failed: {_mask(done.stderr.strip())[:300]}")
    return json.loads(done.stdout or "{}")


def _tag_filter(key: str, value: str) -> str:
    return f"Name=tag:{key},Values={value}"


def fetch() -> Snapshot:
    snap = Snapshot()

    # The label catch-all: anything at all that carries Env=lab-eks.
    for r in _aws(
        "resourcegroupstaggingapi", "get-resources", "--tag-filters", f"Key=Env,Values={ENV}"
    )["ResourceTagMappingList"]:
        parts = r["ResourceARN"].split(":", 5)
        kind = f"{parts[2]}/{re.split('[/:]', parts[5])[0]}"
        # An instance listed here may already be terminated (kept about an hour);
        # servers are checked directly below.
        if kind != "ec2/instance":
            snap.tagged.append((kind, parts[5].split("/")[-1]))

    snap.clusters = _aws("eks", "list-clusters")["clusters"]
    snap.clusters = [c for c in snap.clusters if c == NAME]

    for flt in (_tag_filter("Env", ENV), _tag_filter("eks:cluster-name", NAME)):
        for res in _aws("ec2", "describe-instances", "--filters", flt)["Reservations"]:
            for i in res["Instances"]:
                if i["State"]["Name"] != "terminated":
                    snap.instances.append(i["InstanceId"])
    # Disks the disk driver made for the lab's database carry no Terraform label of their own.
    for flt in (
        _tag_filter("Env", ENV),
        _tag_filter("kubernetes.io/created-for/pvc/namespace", "kaval-lab"),
    ):
        for v in _aws("ec2", "describe-volumes", "--filters", flt)["Volumes"]:
            snap.volumes.append(v["VolumeId"])

    for page in _aws("elbv2", "describe-load-balancers")["LoadBalancers"]:
        arn = page["LoadBalancerArn"]
        tags = _aws("elbv2", "describe-tags", "--resource-arns", arn)["TagDescriptions"]
        if any(t["Key"] == LBC_TAG and t["Value"] == NAME for d in tags for t in d["Tags"]):
            snap.load_balancers.append(arn.split("/", 1)[-1])

    snap.vpcs = [
        v["VpcId"]
        for v in _aws("ec2", "describe-vpcs", "--filters", _tag_filter("Name", NAME))["Vpcs"]
    ]
    for vpc in snap.vpcs:
        for eni in _aws(
            "ec2", "describe-network-interfaces", "--filters", f"Name=vpc-id,Values={vpc}"
        )["NetworkInterfaces"]:
            snap.network_interfaces.append(eni["NetworkInterfaceId"])

    for role in _aws("iam", "list-roles", "--query", f"Roles[?starts_with(RoleName, '{NAME}')]"):
        snap.roles.append(role["RoleName"])
    query = f"Functions[?starts_with(FunctionName, '{NAME}')]"
    for fn in _aws("lambda", "list-functions", "--query", query):
        snap.functions.append(fn["FunctionName"])
    params = _aws(
        "ssm",
        "describe-parameters",
        "--parameter-filters",
        f"Key=Name,Option=BeginsWith,Values=/kaval/{NAME}/",
    )["Parameters"]
    snap.parameters = [p["Name"] for p in params]
    return snap


def main(argv: list[str]) -> int:
    findings = verdict(fetch())
    print(_mask(render(findings)))
    return exit_code(findings, billing_only="--billing-only" in argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
