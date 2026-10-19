"""Daily waste scan (KAV-94, ADR-0062).

Once a day this function looks at the account's servers, disks, fixed addresses and snapshots and
writes what it found into ONE parameter (`/kaval/<env>/waste-scan`). The server copies that
parameter into the cluster, and the cost reader there turns each finding into a signal. So:

    this function -> parameter -> the server's timer -> a ConfigMap -> signals -> incidents

and no program in the cluster holds an AWS login. It FINDS and REPORTS. It never stops, deletes,
detaches or changes anything: its role has no such verb (infra/modules/waste-scan/main.tf), and
removing anything is a separate, approved action (KAV-95).

Five kinds of finding, each one for a person:

  volume_unattached        a disk attached to nothing, seen on two daily scans in a row
  address_unattached       a fixed address attached to nothing, same rule (it costs money)
  snapshot_past_retention  a backup copy older than its class allows (never the newest of a disk)
  server_overstay          a staging or dev server running longer than SERVER_MAX_HOURS
  resource_unlabelled      a Kaval resource missing Project, Env or ManagedBy (ADR-0061)

"Seen twice" is the safety against flagging a disk that is mid-move: the scan keeps what it saw last
time in the same parameter. Production servers are never flagged as overstaying (they are meant to
run). Another project's resources (Project is not kaval) are counted and left alone.

What goes into the parameter is IDs, sizes, dates and fixed words, never a tag value or an AWS error
text: the signal rows are copied to staging with the rest of the database and read by a model.
The pure `scan` function does all the deciding, so it is tested without AWS.
"""

import json
import logging
import os
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

log = logging.getLogger()
log.setLevel(logging.INFO)

ENV_NAME = os.environ.get("ENV_NAME", "")
PARAMETER_NAME = os.environ.get("PARAMETER_NAME", "")

# ── the rules (ADR-0062). Every number here is a default the owner can change by editing this file.
SERVER_MAX_HOURS = 12
STOP_COPY_DAYS = 30  # the pre-stop copies Kaval's own jobs make
MANUAL_DAYS = 90  # copies a person made by hand
UNLABELLED_DAYS = 30  # copies nobody can account for
MAX_FINDINGS = 25
MAX_CANDIDATES = 30
# A standard Parameter Store value is 4096 bytes; stay under it with room for the envelope.
LIMIT_BYTES = 4000

# ── the tag policy (ADR-0061). A copy of scripts/ops/tag_policy.py, because this function ships as
# one file; test_waste_scan_lambda.py fails if the two ever differ.
PROJECT = "kaval"
REQUIRED_KEYS = ("Project", "Env", "ManagedBy")
ENVS = ("prod", "staging", "dev", "state")
MANAGED_BY = ("terraform", "kaval-lambda", "manual")
ALLOWED = {"Project": (PROJECT,), "Env": ENVS, "ManagedBy": MANAGED_BY}

# Mumbai gp3, per GB-month (Price List API, 2026-10-08; docs/cost/budget-plan.md) and a public IPv4
# address at $0.005 an hour for a 730-hour month. An estimate for the person reading the finding.
GP3_GB_MONTH = Decimal("0.0912")
ADDRESS_MONTH = Decimal("3.65")

KIND_ORDER = (
    "server_overstay",
    "snapshot_past_retention",
    "volume_unattached",
    "address_unattached",
    "resource_unlabelled",
)
# The removal steps' labels (ADR-0063): written by the removal worker, read here so the cluster's
# planner can see where each disk stands. Kept equal to waste_removal.py by a test.
MARK_TAG = "KavalRemoval"
BACKUP_TAG = "KavalBackupOf"
MARK_FORMAT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
# Snapshots that something else owns: a lifecycle policy's, or the backing of a machine image.
AMI_DESCRIPTIONS = ("Created by CreateImage", "Copied for DestinationAmi")


def tags_of(item: Mapping[str, Any]) -> dict[str, str]:
    return {t["Key"]: t["Value"] for t in item.get("Tags") or []}


def is_kaval(tags: Mapping[str, str]) -> bool:
    """No Project at all counts as Kaval's, so a forgotten resource is looked at, not ignored."""
    return tags.get("Project", PROJECT) == PROJECT


def tag_problems(tags: Mapping[str, str]) -> list[str]:
    """The required keys that are missing or hold a value outside the list (names only)."""
    return [k for k in REQUIRED_KEYS if tags.get(k) not in ALLOWED[k]]


def removal_facts(
    vid: str, tags: Mapping[str, str], snapshots: Iterable[Mapping[str, Any]]
) -> dict[str, str]:
    """Where a disk stands in the removal steps (ADR-0063), read from the labels on AWS itself so a
    rebuilt cluster loses nothing: when it was marked, and the state of the newest backup of it."""
    facts: dict[str, str] = {}
    mark = tags.get(MARK_TAG, "")
    if MARK_FORMAT.fullmatch(mark):
        facts["pending_removal_since"] = mark
    copies = [
        s
        for s in snapshots
        if tags_of(s).get(BACKUP_TAG) == vid and tags_of(s).get("Project") == PROJECT
    ]
    if copies:
        newest_copy = max(copies, key=lambda s: _utc(s["StartTime"]))
        facts["backup_snapshot_id"] = newest_copy["SnapshotId"]
        state = newest_copy.get("State")
        facts["backup_state"] = state if state in ("completed", "pending", "error") else "unknown"
    return facts


def _money(amount: Decimal) -> str:
    return str(amount.quantize(Decimal("0.01")))


def _utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _finding(kind: str, target: str, value: dict[str, Any]) -> dict[str, Any]:
    return {"kind": kind, "target": target, "value": value}


def scan(
    inventory: Mapping[str, Iterable[Mapping[str, Any]]],
    now: datetime,
    env: str,
    previous: Mapping[str, str],
) -> dict[str, Any]:
    """The whole decision. `inventory` is the four describe calls' answers
    (`instances` already flattened out of their reservations); `previous` is what the last scan
    saw unattached, {id: first date seen}. Returns the document that is written."""
    today = now.date()
    findings: list[dict[str, Any]] = []
    candidates: dict[str, str] = {}
    left_alone = 0

    instances = list(inventory.get("instances", []))
    volumes = list(inventory.get("volumes", []))
    addresses = list(inventory.get("addresses", []))
    snapshots = list(inventory.get("snapshots", []))
    live_volumes = {v["VolumeId"] for v in volumes}

    # The newest snapshot of each disk is the only copy that may be all there is: never flagged.
    newest: dict[str, str] = {}
    newest_at: dict[str, datetime] = {}
    for snap in snapshots:
        vol, at = snap.get("VolumeId"), _utc(snap["StartTime"])
        if vol and (vol not in newest_at or at > newest_at[vol]):
            newest_at[vol], newest[vol] = at, snap["SnapshotId"]

    def mine(tags: Mapping[str, str]) -> bool:
        return is_kaval(tags) and tags.get("Env") == env

    for inst in instances:
        tags = tags_of(inst)
        state = inst.get("State", {}).get("Name")
        if state in ("terminated", "shutting-down"):
            continue
        if not is_kaval(tags):
            left_alone += 1
            continue
        if tag_problems(tags):
            findings.append(
                _finding(
                    "resource_unlabelled",
                    f"ec2/{inst['InstanceId']}",
                    {
                        "resource_type": "instance",
                        "resource_id": inst["InstanceId"],
                        "problems": tag_problems(tags),
                    },
                )
            )
        if state == "running" and mine(tags) and env != "prod":
            hours = int((now - _utc(inst["LaunchTime"])).total_seconds() // 3600)
            if hours > SERVER_MAX_HOURS:
                findings.append(
                    _finding(
                        "server_overstay",
                        f"ec2/{inst['InstanceId']}",
                        {
                            "instance_id": inst["InstanceId"],
                            "env": env,
                            "running_hours": hours,
                            "limit_hours": SERVER_MAX_HOURS,
                        },
                    )
                )

    for vol in volumes:
        tags, vid = tags_of(vol), vol["VolumeId"]
        if not is_kaval(tags):
            left_alone += 1
            continue
        if tag_problems(tags):
            findings.append(
                _finding(
                    "resource_unlabelled",
                    f"ebs/{vid}",
                    {"resource_type": "volume", "resource_id": vid, "problems": tag_problems(tags)},
                )
            )
        if mine(tags) and vol.get("State") == "available" and not vol.get("Attachments"):
            first = previous.get(vid)
            candidates[vid] = first or today.isoformat()
            if first:
                value: dict[str, Any] = {
                    "volume_id": vid,
                    "state": "available",
                    "size_gib": int(vol["Size"]),
                    "volume_type": vol.get("VolumeType", "unknown"),
                    "days_unattached": max(1, (today - datetime.fromisoformat(first).date()).days),
                }
                if vol.get("VolumeType") == "gp3":
                    value["monthly_cost_usd"] = _money(GP3_GB_MONTH * int(vol["Size"]))
                value.update(removal_facts(vid, tags, snapshots))
                findings.append(_finding("volume_unattached", f"ebs/{vid}", value))

    for addr in addresses:
        tags, aid = tags_of(addr), addr.get("AllocationId")
        if not aid:
            continue
        if not is_kaval(tags):
            left_alone += 1
            continue
        if tag_problems(tags):
            findings.append(
                _finding(
                    "resource_unlabelled",
                    f"eip/{aid}",
                    {
                        "resource_type": "address",
                        "resource_id": aid,
                        "problems": tag_problems(tags),
                    },
                )
            )
        if mine(tags) and not addr.get("AssociationId") and not addr.get("InstanceId"):
            first = previous.get(aid)
            candidates[aid] = first or today.isoformat()
            if first:
                findings.append(
                    _finding(
                        "address_unattached",
                        f"eip/{aid}",
                        {
                            "allocation_id": aid,
                            "days_unattached": max(
                                1, (today - datetime.fromisoformat(first).date()).days
                            ),
                            "monthly_cost_usd": _money(ADDRESS_MONTH),
                        },
                    )
                )

    for snap in snapshots:
        tags, sid = tags_of(snap), snap["SnapshotId"]
        if not is_kaval(tags):
            left_alone += 1
            continue
        if tag_problems(tags):
            findings.append(
                _finding(
                    "resource_unlabelled",
                    f"snapshot/{sid}",
                    {
                        "resource_type": "snapshot",
                        "resource_id": sid,
                        "problems": tag_problems(tags),
                    },
                )
            )
        if snap.get("State") != "completed" or "dlm:managed" in tags:
            continue
        if str(snap.get("Description", "")).startswith(AMI_DESCRIPTIONS):
            continue
        # A removal's own backup of a disk that no longer exists expires like any copy (ADR-0063
        # decision 9); otherwise it would be "the newest copy of a disk" for ever.
        gone_backup = bool(tags.get(BACKUP_TAG)) and tags[BACKUP_TAG] not in live_volumes
        if newest.get(snap.get("VolumeId") or "") == sid and not gone_backup:
            continue
        snap_env = tags.get("Env")
        if snap_env != env and snap_env in ENVS:
            continue  # another environment's copy: its own scan reports it
        if snap_env not in ENVS or tags.get("ManagedBy") not in MANAGED_BY:
            klass, limit = "unlabelled", UNLABELLED_DAYS
        elif tags["ManagedBy"] == "kaval-lambda":
            klass, limit = "stop_copy", STOP_COPY_DAYS
        else:
            klass, limit = "manual", MANUAL_DAYS
        age = (now - _utc(snap["StartTime"])) // timedelta(days=1)
        if age > limit:
            findings.append(
                _finding(
                    "snapshot_past_retention",
                    f"snapshot/{sid}",
                    {
                        "snapshot_id": sid,
                        "class": klass,
                        "age_days": age,
                        "limit_days": limit,
                        "size_gib": int(snap.get("VolumeSize", 0)),
                    },
                )
            )

    findings.sort(key=lambda f: (KIND_ORDER.index(f["kind"]), f["target"]))
    truncated = max(0, len(findings) - MAX_FINDINGS)
    doc: dict[str, Any] = {
        "status": "ok",
        "scanned_at": _stamp(now),
        "env": env,
        "findings": findings[:MAX_FINDINGS],
        "candidates": dict(sorted(candidates.items())[:MAX_CANDIDATES]),
        "left_alone": left_alone,
        "truncated": truncated,
    }
    return fit(doc)


def encode(doc: Mapping[str, Any]) -> str:
    return json.dumps(doc, separators=(",", ":"), sort_keys=True)


def fit(doc: dict[str, Any]) -> dict[str, Any]:
    """Drop findings from the end (the least important kinds sort last) until the document fits a
    standard parameter, counting what was dropped so the reader can say so."""
    while len(encode(doc).encode("utf-8")) > LIMIT_BYTES and doc["findings"]:
        doc["findings"].pop()
        doc["truncated"] += 1
    return doc


def failed(now: datetime, env: str, reason: str, candidates: Mapping[str, str]) -> dict[str, Any]:
    """The document for a scan that could not finish. `reason` is a fixed word, never AWS's text.
    The earlier candidates are kept so one bad day does not restart the two-scan rule."""
    return {
        "status": "failed",
        "reason": reason,
        "scanned_at": _stamp(now),
        "env": env,
        "candidates": dict(candidates),
        "findings": [],
        "left_alone": 0,
        "truncated": 0,
    }


# ── the AWS half: four read calls and one write of our own parameter ────────────────────────────


def _inventory(ec2: Any) -> dict[str, list[dict[str, Any]]]:
    instances: list[dict[str, Any]] = []
    for page in ec2.get_paginator("describe_instances").paginate():
        for reservation in page["Reservations"]:
            instances.extend(reservation["Instances"])
    volumes = [v for p in ec2.get_paginator("describe_volumes").paginate() for v in p["Volumes"]]
    snapshots = [
        s
        for p in ec2.get_paginator("describe_snapshots").paginate(OwnerIds=["self"])
        for s in p["Snapshots"]
    ]
    return {
        "instances": instances,
        "volumes": volumes,
        "snapshots": snapshots,
        "addresses": ec2.describe_addresses()["Addresses"],
    }


def _previous(ssm: Any) -> dict[str, str]:
    """What the last scan saw unattached. Anything unreadable is "nothing", which only delays a
    report by a day; it can never report something early."""
    try:
        raw = ssm.get_parameter(Name=PARAMETER_NAME)["Parameter"]["Value"]
        seen = json.loads(raw).get("candidates", {})
        return {
            str(k): str(v)
            for k, v in seen.items()
            if isinstance(v, str) and datetime.fromisoformat(v)
        }
    except Exception:  # noqa: BLE001 - see the docstring
        log.warning("No readable earlier scan; starting the two-scan rule over.")
        return {}


def _write(ssm: Any, doc: Mapping[str, Any]) -> None:
    ssm.put_parameter(Name=PARAMETER_NAME, Value=encode(doc), Type="String", Overwrite=True)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    import boto3

    now = datetime.now(UTC)
    ec2, ssm = boto3.client("ec2"), boto3.client("ssm")
    previous = _previous(ssm)
    try:
        doc = scan(_inventory(ec2), now, ENV_NAME, previous)
    except Exception:
        log.exception("The scan could not read the account.")
        _write(ssm, failed(now, ENV_NAME, "describe_failed", previous))
        raise  # a failed run is retried by the schedule, and shows in the function's own metrics
    _write(ssm, doc)
    counts: dict[str, int] = {}
    for f in doc["findings"]:
        counts[f["kind"]] = counts.get(f["kind"], 0) + 1
    log.info(
        "waste scan %s: %s (truncated=%d left_alone=%d)",
        ENV_NAME,
        counts or "clean",
        doc["truncated"],
        doc["left_alone"],
    )
    return {"status": "ok", "findings": counts, "truncated": doc["truncated"]}
