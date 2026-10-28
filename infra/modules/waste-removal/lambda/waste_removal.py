"""The removal worker (KAV-95, ADR-0063): the only thing in Kaval that can delete waste in AWS.

A person approved an order in the app; the executor wrote it into the cluster; the server's timer
put it into ONE parameter (`/kaval/<env>/removal-orders`); that wakes this function, which acts
and writes its answer into ANOTHER (`/kaval/<env>/removal-result`), which the timer copies back:

    approval -> executor -> ConfigMap -> server's timer -> parameter -> THIS FUNCTION
                                                                          |
    signal <- cost reader's pod <- ConfigMap <- server's timer <- parameter

No program in the cluster holds an AWS login. This function holds the only login that can change
anything, and it is narrow (infra/modules/waste-removal/main.tf): it can make a backup copy of a
Kaval disk, label it, and delete one disk or one backup copy that is labelled Kaval's and not
Terraform's.

Three steps, each one order naming one thing:

  mark_backup      a loose disk: take a backup copy, then label the disk `KavalRemoval`
  delete_volume    that disk, once marked for the grace period and the backup is finished
  delete_snapshot  a backup copy past its retention (never the newest of a disk that exists)

The order is only a request. Before acting, `decide` reads AWS's own answer again and refuses on any
doubt, so a stale, forged or mistaken order can only remove something that passes every rule now.
A missing `Project` label means NOT Kaval's here (the scan reads it the other way, to flag it).

What goes into the result is ids, dates and fixed words, never a tag value or AWS's error text: the
signal rows are copied to staging with the rest of the database and read by a model.
The pure `decide` does all the deciding, so it is tested without AWS.
"""

import json
import logging
import os
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple

log = logging.getLogger()
log.setLevel(logging.INFO)

ENV_NAME = os.environ.get("ENV_NAME", "")
ORDERS_PARAMETER = os.environ.get("ORDERS_PARAMETER", "")
RESULT_PARAMETER = os.environ.get("RESULT_PARAMETER", "")
# "false" refuses every order with the word `disabled`: the one switch (ADR-0063 decision 10).
ENABLED = os.environ.get("ENABLED", "true").lower() == "true"
# How long a disk stays marked before it may be deleted. 168 hours (7 days) in production.
GRACE_HOURS = float(os.environ.get("GRACE_HOURS", "168"))

PROJECT = "kaval"
# Only these two may be removed. `terraform` is Terraform's (it would recreate it or drift);
# anything else, or nothing, is not labelled well enough to act on.
REMOVABLE_MANAGED_BY = ("manual", "kaval-lambda")
MARK_TAG = "KavalRemoval"
BACKUP_TAG = "KavalBackupOf"

# The retention rules, copied from waste_scan.py (one file each, so no import); the tests fail if
# the two ever differ.
STOP_COPY_DAYS = 30
MANUAL_DAYS = 90
UNLABELLED_DAYS = 30
ENVS = ("prod", "staging", "dev", "state", "lab-eks")
MANAGED_BY = ("terraform", "kaval-lambda", "manual")
AMI_DESCRIPTIONS = ("Created by CreateImage", "Copied for DestinationAmi")

STEPS = ("mark_backup", "delete_volume", "delete_snapshot")
MAX_ORDERS_PER_RUN = 5
MAX_RESULTS = 10
LIMIT_BYTES = 4000  # a standard parameter is 4096
VOLUME_ID = re.compile(r"vol-[0-9a-f]{8,17}")
SNAPSHOT_ID = re.compile(r"snap-[0-9a-f]{8,17}")
ORDER_ID = re.compile(r"[0-9a-f-]{8,40}")
GP3_GB_MONTH = "0.0912"  # Mumbai, per GB-month; the saving shown to the person (an estimate)

# `fullmatch`, never `match` with a `$`: a `$` also accepts a trailing newline.
# The fixed words a refusal may carry. Nothing else ever appears in a result.
REASONS = (
    "ok",
    "disabled",
    "bad_order",
    "not_found",
    "not_kaval",
    "other_env",
    "terraform_owned",
    "not_labelled",
    "attached",
    "already_marked",
    "not_marked",
    "grace_not_over",
    "no_backup",
    "backup_unfinished",
    "not_past_retention",
    "newest_copy",
    "in_use_by_image",
    "aws_failed",
)


class Decision(NamedTuple):
    reason: str  # "ok" means act; anything else is the refusal's fixed word
    backup_of: str = ""  # for mark_backup: the disk to copy
    saved_usd: str = ""  # for delete_volume: the monthly saving, shown back to the person


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _stamp(now: datetime) -> str:
    return now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def tags_of(item: Mapping[str, Any]) -> dict[str, str]:
    return {t["Key"]: t["Value"] for t in item.get("Tags") or []}


# ── the orders (untrusted: they came out of a parameter) ────────────────────────────────────────


def parse_orders(raw: str) -> list[dict[str, str]]:
    """The valid orders in the parameter. Anything that is not exactly {id, step, target} with the
    right shapes is dropped here; one with a readable id but a wrong step or target is kept and
    answered `bad_order` by `decide`. The function never acts on text it did not recognise."""
    try:
        doc = json.loads(raw)
    except ValueError:
        return []
    items = doc.get("orders") if isinstance(doc, dict) else None
    out: list[dict[str, str]] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        oid, step, target = item.get("id"), item.get("step"), item.get("target")
        if not (isinstance(oid, str) and ORDER_ID.fullmatch(oid)):
            continue
        out.append({"id": oid, "step": str(step), "target": str(target)})
    return out


def well_formed(order: Mapping[str, str]) -> bool:
    step, target = order["step"], order["target"]
    if step in ("mark_backup", "delete_volume"):
        return bool(VOLUME_ID.fullmatch(target))
    return step == "delete_snapshot" and bool(SNAPSHOT_ID.fullmatch(target))


# ── the rules ───────────────────────────────────────────────────────────────────────────────────


def _ownership(item: Mapping[str, Any], env: str) -> str:
    """ "" if the thing is Kaval's, in this environment, and not Terraform's; otherwise the word."""
    tags = tags_of(item)
    if tags.get("Project") != PROJECT:
        return "not_kaval"
    if tags.get("Env") not in ENVS:
        return "not_labelled"
    if tags["Env"] != env:
        return "other_env"
    managed = tags.get("ManagedBy")
    if managed == "terraform":
        return "terraform_owned"
    if managed not in REMOVABLE_MANAGED_BY:
        return "not_labelled"
    return ""


def _loose(volume: Mapping[str, Any]) -> bool:
    return volume.get("State") == "available" and not volume.get("Attachments")


def _marked_at(volume: Mapping[str, Any]) -> datetime | None:
    value = tags_of(volume).get(MARK_TAG, "")
    try:
        return _utc(datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return None


def _backups_of(volume_id: str, snapshots: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    out = []
    for s in snapshots:
        t = tags_of(s)
        if t.get(BACKUP_TAG) == volume_id and t.get("Project") == PROJECT:
            out.append(s)
    return out


def decide(
    order: Mapping[str, str],
    volumes: Iterable[Mapping[str, Any]],
    snapshots: Iterable[Mapping[str, Any]],
    now: datetime,
    env: str,
    grace_hours: float,
    enabled: bool = True,
) -> Decision:
    """Everything this function will ever do is decided here, from AWS's current answer."""
    if not enabled:
        return Decision("disabled")
    if not well_formed(order):
        return Decision("bad_order")
    volumes, snapshots = list(volumes), list(snapshots)
    if order["step"] == "delete_snapshot":
        return _decide_snapshot(order["target"], volumes, snapshots, now, env)
    vol = next((v for v in volumes if v["VolumeId"] == order["target"]), None)
    if vol is None:
        return Decision("not_found")
    if word := _ownership(vol, env):
        return Decision(word)
    if not _loose(vol):
        return Decision("attached")
    marked = _marked_at(vol)
    if order["step"] == "mark_backup":
        if MARK_TAG in tags_of(vol):
            return Decision("already_marked")
        return Decision("ok", backup_of=vol["VolumeId"])
    # delete_volume: marked long enough, with a finished backup copy of this very disk
    if marked is None:
        return Decision("not_marked")
    if now - marked < timedelta(hours=grace_hours):
        return Decision("grace_not_over")
    backups = _backups_of(vol["VolumeId"], snapshots)
    if not backups:
        return Decision("no_backup")
    if not any(b.get("State") == "completed" for b in backups):
        return Decision("backup_unfinished")
    saved = ""
    if vol.get("VolumeType") == "gp3":
        saved = f"{int(vol.get('Size', 0)) * float(GP3_GB_MONTH):.2f}"
    return Decision("ok", saved_usd=saved)


def _decide_snapshot(
    sid: str,
    volumes: list[Mapping[str, Any]],
    snapshots: list[Mapping[str, Any]],
    now: datetime,
    env: str,
) -> Decision:
    snap = next((s for s in snapshots if s["SnapshotId"] == sid), None)
    if snap is None:
        return Decision("not_found")
    tags = tags_of(snap)
    if tags.get("Project") != PROJECT:
        return Decision("not_kaval")
    if tags.get("ManagedBy") == "terraform":
        return Decision("terraform_owned")
    snap_env = tags.get("Env")
    if snap_env in ENVS and snap_env != env:
        return Decision("other_env")
    if snap.get("State") != "completed":
        return Decision("backup_unfinished")
    if "dlm:managed" in tags or str(snap.get("Description", "")).startswith(AMI_DESCRIPTIONS):
        return Decision("in_use_by_image")
    live = {v["VolumeId"] for v in volumes}
    source = snap.get("VolumeId") or ""
    # The newest copy of a disk that still exists may be all there is: never removed. A removal's
    # own backup of a disk that is gone is the exception (ADR-0063 decision 9): it expires like
    # any copy.
    removal_backup = bool(tags.get(BACKUP_TAG)) and tags[BACKUP_TAG] not in live
    if not removal_backup:
        at = _utc(snap["StartTime"])
        newer = [s for s in snapshots if s.get("VolumeId") == source and _utc(s["StartTime"]) > at]
        if not newer:
            return Decision("newest_copy")
    if snap_env not in ENVS or tags.get("ManagedBy") not in MANAGED_BY:
        limit = UNLABELLED_DAYS
    elif tags["ManagedBy"] == "kaval-lambda":
        limit = STOP_COPY_DAYS
    else:
        limit = MANUAL_DAYS
    if (now - _utc(snap["StartTime"])) // timedelta(days=1) <= limit:
        return Decision("not_past_retention")
    return Decision("ok")


# ── the documents ───────────────────────────────────────────────────────────────────────────────


def encode(doc: Mapping[str, Any]) -> str:
    return json.dumps(doc, separators=(",", ":"), sort_keys=True)


def result_entry(
    order: Mapping[str, str], status: str, reason: str, now: datetime, **extra: str
) -> dict[str, str]:
    assert reason in REASONS, reason
    entry = {
        "id": order["id"],
        "step": order["step"],
        "target": order["target"],
        "status": status,
        "reason": reason,
        "at": _stamp(now),
    }
    entry.update({k: v for k, v in extra.items() if v})
    return entry


def result_document(env: str, results: list[dict[str, str]], now: datetime) -> dict[str, Any]:
    """The newest results that fit a parameter. Older ones fall off the end."""
    doc: dict[str, Any] = {"env": env, "results": results[-MAX_RESULTS:], "written_at": _stamp(now)}
    while len(encode(doc).encode("utf-8")) > LIMIT_BYTES and doc["results"]:
        doc["results"].pop(0)
    return doc


# ── the AWS half ────────────────────────────────────────────────────────────────────────────────


def _inventory(ec2: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    volumes = [v for p in ec2.get_paginator("describe_volumes").paginate() for v in p["Volumes"]]
    snapshots = [
        s
        for p in ec2.get_paginator("describe_snapshots").paginate(OwnerIds=["self"])
        for s in p["Snapshots"]
    ]
    return volumes, snapshots


def _act(
    ec2: Any, order: Mapping[str, str], decision: Decision, now: datetime, env: str
) -> dict[str, str]:
    """The only calls that change anything; each is named in the role (main.tf), with conditions."""
    if order["step"] == "mark_backup":
        snap = ec2.create_snapshot(
            VolumeId=decision.backup_of,
            Description="Kaval removal backup",
            TagSpecifications=[
                {
                    "ResourceType": "snapshot",
                    "Tags": [
                        {"Key": "Project", "Value": PROJECT},
                        {"Key": "Env", "Value": env},
                        {"Key": "ManagedBy", "Value": "kaval-lambda"},
                        {"Key": "Name", "Value": f"kaval-removal-backup-{decision.backup_of}"},
                        {"Key": BACKUP_TAG, "Value": decision.backup_of},
                    ],
                }
            ],
        )
        ec2.create_tags(
            Resources=[decision.backup_of], Tags=[{"Key": MARK_TAG, "Value": _stamp(now)}]
        )
        return {"backup": snap["SnapshotId"]}
    if order["step"] == "delete_volume":
        ec2.delete_volume(VolumeId=order["target"])
        return {"saved_usd": decision.saved_usd}
    ec2.delete_snapshot(SnapshotId=order["target"])
    return {}


def _read(ssm: Any, name: str) -> str:
    try:
        return str(ssm.get_parameter(Name=name)["Parameter"]["Value"])
    except Exception:  # noqa: BLE001 - a parameter not made yet reads as "nothing"
        return ""


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    import boto3

    now = datetime.now(UTC)
    ec2, ssm = boto3.client("ec2"), boto3.client("ssm")
    orders = parse_orders(_read(ssm, ORDERS_PARAMETER))
    try:
        earlier = json.loads(_read(ssm, RESULT_PARAMETER) or "{}").get("results", [])
        earlier = [r for r in earlier if isinstance(r, dict) and isinstance(r.get("id"), str)]
    except ValueError:
        earlier = []
    seen = {r["id"] for r in earlier}
    todo = [o for o in orders if o["id"] not in seen][:MAX_ORDERS_PER_RUN]
    if not todo:
        return {"status": "idle", "orders": 0}
    results = list(earlier)
    try:
        volumes, snapshots = _inventory(ec2)
    except Exception:
        log.exception("could not read the account; no order was acted on")
        for o in todo:
            results.append(result_entry(o, "failed", "aws_failed", now))
        _put(ssm, result_document(ENV_NAME, results, now))
        raise
    for order in todo:
        decision = decide(order, volumes, snapshots, now, ENV_NAME, GRACE_HOURS, ENABLED)
        if decision.reason != "ok":
            log.info("order %s %s refused: %s", order["id"], order["step"], decision.reason)
            results.append(result_entry(order, "refused", decision.reason, now))
            continue
        try:
            extra = _act(ec2, order, decision, now, ENV_NAME)
        except Exception:
            log.exception("order %s %s failed", order["id"], order["step"])
            results.append(result_entry(order, "failed", "aws_failed", now))
            continue
        log.info("order %s %s done", order["id"], order["step"])
        results.append(result_entry(order, "ok", "ok", now, **extra))
    _put(ssm, result_document(ENV_NAME, results, now))
    return {"status": "done", "orders": len(todo)}


def _put(ssm: Any, doc: Mapping[str, Any]) -> None:
    ssm.put_parameter(Name=RESULT_PARAMETER, Value=encode(doc), Type="String", Overwrite=True)
