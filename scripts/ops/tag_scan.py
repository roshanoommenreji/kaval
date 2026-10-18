"""Which of Kaval's resources in the live account are missing a required tag? (KAV-93, ADR-0061)

Read-only. Run by hand with `make tag-scan`; exit status 1 means "at least one Kaval resource is
unlabelled or wrongly labelled". Scheduling it and alerting on it belongs to waste detection
(KAV-94).

Two sources, because neither alone is enough: the tagging service lists everything that has or had a
tag, and EC2 is asked directly for the things that cost money and can carry no tag at all (servers,
disks, snapshots, fixed addresses), since the tagging service cannot show a resource with no tags.

Another project (`stock-trader`) shares the account. Its resources are only counted, never listed as
Kaval's problem and never touched. Account ids are masked in the output.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from tag_policy import is_kaval, tag_problems

REGION = "ap-south-1"
# Things Kaval cannot label: Session Manager creates a session record for every connection.
IGNORED_KINDS = {"ssm/session"}


@dataclass
class Report:
    seen: int = 0
    by_env: Counter[str] = field(default_factory=Counter)
    others: Counter[str] = field(default_factory=Counter)  # another project's, by its Project value
    # kind, id, name, problems
    findings: list[tuple[str, str, str, list[str]]] = field(default_factory=list)


def _mask(text: str) -> str:
    return re.sub(r"\d{12}", "<acct>", text)


def scan(resources: list[dict[str, Any]]) -> Report:
    """resources: dicts with kind, id and tags, in any order; one may repeat."""
    report = Report()
    done: set[tuple[str, str]] = set()
    for r in resources:
        key = (r["kind"], r["id"])
        if key in done or r["kind"] in IGNORED_KINDS:
            continue
        done.add(key)
        report.seen += 1
        tags = r["tags"]
        if not is_kaval(tags):
            report.others[tags["Project"]] += 1
            continue
        report.by_env[tags.get("Env", "(none)")] += 1
        problems = tag_problems(tags)
        if problems:
            report.findings.append((r["kind"], r["id"], tags.get("Name", ""), problems))
    report.findings.sort()
    return report


def render(report: Report) -> str:
    kaval = report.seen - sum(report.others.values())
    lines = [f"Kaval resources checked: {kaval}. Missing or wrong labels: {len(report.findings)}."]
    lines.append("By Env: " + ", ".join(f"{k} {v}" for k, v in sorted(report.by_env.items())))
    for kind, rid, name, problems in report.findings:
        lines.append(f"  {kind:<22} {rid:<24} {name[:34]:<34} {'; '.join(problems)}")
    if report.others:
        lines.append(
            "Not Kaval's, left alone: "
            + ", ".join(f"{v} belonging to {k}" for k, v in sorted(report.others.items()))
        )
    return "\n".join(lines)


# ── fetching (the only part that talks to AWS) ───────────────────────────────────────────────────


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


def _tags(items: list[dict[str, str]] | None) -> dict[str, str]:
    return {t["Key"]: t["Value"] for t in items or []}


def fetch() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in _aws("resourcegroupstaggingapi", "get-resources")["ResourceTagMappingList"]:
        parts = r["ResourceARN"].split(":", 5)
        kind = f"{parts[2]}/{parts[5].split('/')[0].split(':')[0]}"
        out.append({"kind": kind, "id": parts[5].split("/")[-1], "tags": _tags(r["Tags"])})
    for res in _aws("ec2", "describe-instances")["Reservations"]:
        for i in res["Instances"]:
            if i["State"]["Name"] != "terminated":
                tags = _tags(i.get("Tags"))
                out.append({"kind": "ec2/instance", "id": i["InstanceId"], "tags": tags})
    for v in _aws("ec2", "describe-volumes")["Volumes"]:
        out.append({"kind": "ec2/volume", "id": v["VolumeId"], "tags": _tags(v.get("Tags"))})
    for s in _aws("ec2", "describe-snapshots", "--owner-ids", "self")["Snapshots"]:
        out.append({"kind": "ec2/snapshot", "id": s["SnapshotId"], "tags": _tags(s.get("Tags"))})
    for a in _aws("ec2", "describe-addresses")["Addresses"]:
        ip_id = a.get("AllocationId", "")
        out.append({"kind": "ec2/elastic-ip", "id": ip_id, "tags": _tags(a.get("Tags"))})
    return out


def main() -> int:
    report = scan(fetch())
    print(_mask(render(report)))
    return 1 if report.findings else 0


if __name__ == "__main__":
    sys.exit(main())
