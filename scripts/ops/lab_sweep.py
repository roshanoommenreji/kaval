"""Is anything of the EKS lab still in the account? A sweep by label, not by memory. (ADR-0067)

Read-only. Run by hand with `make lab-sweep` after `make lab-down`, and again 24 hours later (the
Phase 7 exit gate: "teardown leaves zero billable resources"). Exit 0 only if nothing is left.

`terraform destroy` removes what Terraform made. It does not remove what the cluster made itself:
the load balancer controller makes load balancers, the disk driver makes disks, and neither is in
Terraform's record. Those are the resources that quietly bill after a "clean" destroy, so this looks
for them by the labels they carry (Env=lab-eks, or the controller's cluster label), not by asking
Terraform what it remembers.

The label index (the Resource Groups Tagging API) lags behind a deletion: after the lab was
destroyed it kept listing two disks that EC2 itself said did not exist. So each tagged thing that
its own service can be asked about is confirmed there first. "Not found" drops it, and the output
names what was dropped; any other answer (an error, a timeout, "it exists") keeps it reported.

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
    stale: list[tuple[str, str]] = field(default_factory=list)  # tagged, but the service says gone


# How to ask each kind's own service whether a tagged thing still exists: the describe call, the
# flag that names the thing, and the service's plain "not found" error.
EXISTENCE_CHECKS: dict[str, tuple[tuple[str, ...], str, str]] = {
    "ec2/volume": (("ec2", "describe-volumes"), "--volume-ids", "InvalidVolume.NotFound"),
    "ec2/snapshot": (("ec2", "describe-snapshots"), "--snapshot-ids", "InvalidSnapshot.NotFound"),
    "ec2/network-interface": (
        ("ec2", "describe-network-interfaces"),
        "--network-interface-ids",
        "InvalidNetworkInterfaceID.NotFound",
    ),
    "ec2/elastic-ip": (
        ("ec2", "describe-addresses"),
        "--allocation-ids",
        "InvalidAllocationID.NotFound",
    ),
    # A deleted NAT gateway stays describable for a while, with the state "deleted".
    "ec2/natgateway": (("ec2", "describe-nat-gateways"), "--nat-gateway-ids", "NatGatewayNotFound"),
}


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


def render_stale(stale: list[tuple[str, str]]) -> str:
    if not stale:
        return ""
    names = ", ".join(f"{kind} {ident}" for kind, ident in stale)
    return (
        f"note: ignored {len(stale)} entry(ies) the label index still lists but whose own service "
        f"says no longer exist: {names}"
    )


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


def _run(args: list[str]) -> Any:
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", check=False)


def _aws(*args: str) -> Any:
    done = _run(["aws", "--region", REGION, *args, "--output", "json"])
    if done.returncode != 0:
        raise SystemExit(f"aws {' '.join(args[:2])} failed: {_mask(done.stderr.strip())[:300]}")
    return json.loads(done.stdout or "{}")


def _is_gone(kind: str, ident: str) -> bool:
    """True only when the thing's own service says it does not exist; anything else is "keep"."""
    check = EXISTENCE_CHECKS.get(kind)
    if check is None:
        return False
    command, flag, not_found = check
    done = _run(["aws", "--region", REGION, *command, flag, ident, "--output", "json"])
    if done.returncode != 0:
        return not_found in (done.stderr or "")
    if kind == "ec2/natgateway":
        gateways = json.loads(done.stdout or "{}").get("NatGateways", [])
        return bool(gateways) and all(g.get("State") == "deleted" for g in gateways)
    return False


def drop_gone(
    tagged: list[tuple[str, str]],
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Split the label index's answer into (still there or unknown, confirmed gone)."""
    kept: list[tuple[str, str]] = []
    stale: list[tuple[str, str]] = []
    for kind, ident in tagged:
        (stale if _is_gone(kind, ident) else kept).append((kind, ident))
    return kept, stale


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

    snap.tagged, snap.stale = drop_gone(snap.tagged)

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
    snap = fetch()
    findings = verdict(snap)
    print(_mask(render(findings)))
    if snap.stale:
        print(_mask(render_stale(snap.stale)))
    return exit_code(findings, billing_only="--billing-only" in argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
