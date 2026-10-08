"""Smoke-test the image set staging is running, and record that it passed (KAV-62, ADR-0031).

Stage one of release.yml (KAV-61) publishes five images and pins staging to the tag. Nothing yet
proved that tag *runs*. This does, read-only, against a staging that is up:

  1. every pod is Running and ready (or a finished job), and none has restarted;
  2. every service container runs the tag Git says staging should run;
  3. the digest the node actually pulled equals the digest ECR holds for that tag, so what ran is
     what was published, byte for byte (not merely a tag that might have been re-pointed);
  4. the gateway reaches the database, at the migration revision the tag's own commit expects;
  5. the gateway's REST API answers reads.

On a pass it appends a record to deploy/promotion/passed-staging.json: the digests that passed,
the checks, and what the pass does NOT prove. `promote.yml` (not built yet) will refuse any digest
that is not in that file. The record is committed through a pull request like any other change.

    python scripts/release/staging_smoke.py            # the tag in staging's HelmRelease
    python scripts/release/staging_smoke.py --no-record --tag sha-ffb436b   # a deliberate fail

The node is reached over Session Manager (no SSH, no open port). Nothing is changed on it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RECORD = "deploy/promotion/passed-staging.json"
HELMRELEASE = "deploy/gitops/staging/helmrelease.yaml"
PREFIX = "kaval-staging"
NAMESPACE = "kaval-staging"

# gateway, agent, executor, collector run in staging. backup is published by release.yml but is
# not deployed there (it needs prod's dump bucket), so it is recorded and not exercised.
SERVICES = ("gateway", "agent", "executor", "collector")
NOT_EXERCISED = ("backup",)

# A fresh staging has an empty database. The migration job creates the schema first and only then
# does the node set each service's role password (KAV-56), so a service that connects before that
# is refused and restarts. That is the designed first-boot sequence, not a fault, so a couple of
# restarts are tolerated and recorded. A real crash loop keeps counting past this.
MAX_RESTARTS = 2

# What a green smoke test still says nothing about. Written into every record so nobody reads
# "passed staging" as more than it is.
NOT_COVERED = (
    "Ollama and the model: no environment deploys one yet, so /healthz's ollama check is "
    "expected to fail and is not gated on",
    "agent diagnosis and proposals: they need the model",
    "the backup image: published, not deployed to staging",
    "Slack approval: staging has no Slack app of its own",
)

TAG_RE = re.compile(r"^sha-[0-9a-f]{7,40}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
IMAGE_RE = re.compile(r"/kaval/([a-z]+):([A-Za-z0-9._-]+)$")
IMAGE_ID_RE = re.compile(r"/kaval/([a-z]+)@(sha256:[0-9a-f]{64})$")

# One shell snippet, run on the node. Output is split on the ## markers. jsonpath rather than
# JSON because Session Manager returns at most ~24k characters of output.
REMOTE = r"""
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
K=/usr/local/bin/kubectl
NS=%(ns)s
echo '##pods'
$K get pods -n $NS -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.phase}{"\t"}{range .status.containerStatuses[*]}{.image}{"\t"}{.imageID}{"\t"}{.ready}{"\t"}{.restartCount}{"\t"}{end}{"\n"}{end}'
gw=$($K get svc -n $NS -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.clusterIP}:{.spec.ports[0].port}{"\n"}{end}' | awk '$1 ~ /-gateway$/ {print $2; exit}')
echo '##healthz'
curl -s -m 10 -w '\n%%{http_code}\n' "http://$gw/healthz"
for p in '/v1/incidents?limit=1' '/v1/signals?limit=1'; do
  echo "##http $p"
  curl -s -m 10 -o /dev/null -w '%%{http_code}\n' "http://$gw$p"
done
echo '##end'
"""  # noqa: E501 (jsonpath is one long expression)


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


# ── pure parts: parsing what the node said, and judging it ─────────────────────────────


def parse_observation(text: str) -> dict[str, Any]:
    """Turn the node's output into plain data. Never raises on odd output: it records what it saw
    and lets `evaluate` fail the check, because a smoke test that crashes proves nothing."""
    sections: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        if line.startswith("##"):
            current = line[2:].strip()
            sections[current] = []
        elif current is not None:
            sections[current].append(line)

    pods = []
    for line in sections.get("pods", []):
        parts = line.rstrip("\t").split("\t")
        if len(parts) < 2 or not parts[0]:
            continue
        containers = []
        fields = parts[2:]
        for i in range(0, len(fields) - len(fields) % 4, 4):
            image, image_id, ready, restarts = fields[i : i + 4]
            containers.append(
                {
                    "image": image,
                    "image_id": image_id,
                    "ready": ready == "true",
                    "restarts": int(restarts) if restarts.isdigit() else -1,
                }
            )
        pods.append({"name": parts[0], "phase": parts[1], "containers": containers})

    body_lines = [ln for ln in sections.get("healthz", []) if ln != ""]
    health_code = body_lines[-1] if body_lines else ""
    health_body = None
    if len(body_lines) > 1:
        try:
            health_body = json.loads("\n".join(body_lines[:-1]))
        except json.JSONDecodeError:
            health_body = None

    http = {}
    for key, lines in sections.items():
        if key.startswith("http "):
            http[key[5:]] = next((ln for ln in lines if ln), "")

    return {"pods": pods, "health_code": health_code, "health": health_body, "http": http}


def alembic_head(revisions: dict[str, str | None]) -> str | None:
    """The one revision no other revision descends from. `revisions` maps id to down_revision."""
    parents = {p for p in revisions.values() if p}
    heads = [r for r in revisions if r not in parents]
    return heads[0] if len(heads) == 1 else None


def _restart_counts(pods: list[dict[str, Any]]) -> list[int]:
    return [c["restarts"] for p in pods for c in p["containers"] if c["restarts"] != 0]


def evaluate(
    observed: dict[str, Any], tag: str, digests: dict[str, str], expected_head: str | None
) -> list[Check]:
    """Judge one observation. `digests` is what ECR holds for `tag`, per service."""
    checks: list[Check] = []
    pods = observed["pods"]

    # 1. pods
    bad = []
    for pod in pods:
        if pod["phase"] == "Succeeded":
            continue  # a finished job (the migration)
        if pod["phase"] != "Running" or not pod["containers"]:
            bad.append(f"{pod['name']} is {pod['phase']}")
        elif not all(c["ready"] for c in pod["containers"]):
            bad.append(f"{pod['name']} is not ready")
    running = [p for p in pods if p["phase"] == "Running"]
    if len(running) < len(SERVICES):
        bad.append(f"only {len(running)} running pods, expected at least {len(SERVICES)}")
    checks.append(
        Check("pods running and ready", not bad, "; ".join(bad) or f"{len(running)} running")
    )

    restarts = [
        f"{p['name']} restarted {c['restarts']}x"
        for p in pods
        for c in p["containers"]
        if c["restarts"] != 0
    ]
    too_many = [r for r, c in zip(restarts, _restart_counts(pods), strict=True) if c > MAX_RESTARTS]
    note = "; ".join(restarts) or "0 restarts"
    if restarts and not too_many:
        note += f" (within the first-boot tolerance of {MAX_RESTARTS})"
    checks.append(Check("no crash loop", not too_many, note))

    # 2 + 3. which tag, and which bytes
    seen_tag: dict[str, set[str]] = {s: set() for s in SERVICES}
    seen_digest: dict[str, set[str]] = {s: set() for s in SERVICES}
    for pod in pods:
        for c in pod["containers"]:
            m = IMAGE_RE.search(c["image"])
            if m and m.group(1) in seen_tag:
                seen_tag[m.group(1)].add(m.group(2))
                mid = IMAGE_ID_RE.search(c["image_id"])
                seen_digest[m.group(1)].add(mid.group(2) if mid else f"?{c['image_id']}")

    wrong_tag = [f"{s} runs {sorted(t) or 'nothing'}" for s, t in seen_tag.items() if t != {tag}]
    checks.append(Check(f"every service runs {tag}", not wrong_tag, "; ".join(wrong_tag) or "ok"))

    wrong_digest = [
        f"{s}: node has {sorted(seen_digest[s]) or 'nothing'}, ECR has {digests.get(s)}"
        for s in SERVICES
        if seen_digest[s] != {digests.get(s)}
    ]
    checks.append(
        Check(
            "running digest equals the ECR digest",
            not wrong_digest,
            "; ".join(wrong_digest) or "all four match",
        )
    )

    # 4. gateway and the database
    health = observed.get("health") or {}
    pg = health.get("postgres") or {}
    detail = str(pg.get("detail", "no answer"))
    revision = detail.removeprefix("migrated to ").strip()
    pg_ok = bool(pg.get("ok")) and detail.startswith("migrated to ")
    if pg_ok and expected_head and revision != expected_head:
        pg_ok = False
        detail = f"database is at {revision}, the tag's commit expects {expected_head}"
    checks.append(Check("gateway reaches the database, migrated", pg_ok, detail))

    # 5. the API
    for path, code in observed.get("http", {}).items():
        checks.append(Check(f"GET {path}", code == "200", f"HTTP {code or 'no answer'}"))
    if not observed.get("http"):
        checks.append(Check("gateway REST reads", False, "no answer"))
    return checks


def build_entry(
    tag: str,
    commit: str | None,
    digests: dict[str, str],
    other: dict[str, str],
    checks: list[Check],
    now: datetime,
) -> dict[str, Any]:
    return {
        "tag": tag,
        "commit": commit,
        "passed_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "images": dict(sorted(digests.items())),
        "published_not_exercised": dict(sorted(other.items())),
        "checks": [{"name": c.name, "detail": c.detail} for c in checks],
        "not_covered": list(NOT_COVERED),
    }


def add_pass(record_text: str | None, entry: dict[str, Any]) -> str:
    """Insert `entry`, replacing an earlier pass of the same tag, and return the file text."""
    data: dict[str, Any] = json.loads(record_text) if record_text else {"schema": 1, "passes": []}
    data["passes"] = [p for p in data["passes"] if p["tag"] != entry["tag"]] + [entry]
    return json.dumps(data, indent=2) + "\n"


def has_passed(record_text: str, digests: dict[str, str]) -> bool:
    """The question promote.yml will ask: did exactly this set of digests pass staging?"""
    for p in json.loads(record_text)["passes"]:
        if all(p["images"].get(s) == d for s, d in digests.items()) and digests:
            return True
    return False


# ── the parts that touch AWS and Git ──────────────────────────────────────────────────


def aws(*args: str) -> str:
    exe = shutil.which("aws") or "aws"
    out = subprocess.run(
        [exe, *args], capture_output=True, text=True, encoding="utf-8", check=False
    )
    if out.returncode != 0:
        raise RuntimeError(f"aws {' '.join(args[:3])}: {out.stderr.strip()[:300]}")
    return out.stdout.strip()


def git(*args: str) -> str:
    out = subprocess.run(
        ["git", *args], capture_output=True, text=True, encoding="utf-8", cwd=ROOT, check=False
    )
    return out.stdout if out.returncode == 0 else ""


def staging_tag() -> str:
    """The tag Git says staging should run (all four are kept equal by pin_staging.py)."""
    text = (ROOT / HELMRELEASE).read_text(encoding="utf-8")
    tags: set[str] = set(re.findall(r"(?m)^\s*tag:\s*(sha-[0-9a-f]{7,40})\s*$", text))
    if len(tags) != 1:
        raise RuntimeError(f"{HELMRELEASE} pins {sorted(tags) or 'no tag'}, expected exactly one")
    return tags.pop()


def ecr_digest(service: str, tag: str) -> str:
    out = aws(
        "ecr", "describe-images", "--repository-name", f"kaval/{service}",
        "--image-ids", f"imageTag={tag}",
        "--query", "imageDetails[0].imageDigest", "--output", "text",
    )  # fmt: skip
    if not DIGEST_RE.match(out):
        raise RuntimeError(f"kaval/{service}:{tag} is not in ECR")
    return out


def commit_head(commit: str) -> str | None:
    """The Alembic head in the tree of `commit`, so the check is against what that build expects."""
    revisions: dict[str, str | None] = {}
    for name in git("ls-tree", "--name-only", commit, "migrations/versions/").split():
        if not name.endswith(".py"):
            continue
        src = git("show", f"{commit}:{name}")
        rev = re.search(r"(?m)^revision[^=]*=\s*['\"]([0-9a-f]+)['\"]", src)
        down = re.search(r"(?m)^down_revision[^=]*=\s*['\"]([0-9a-f]+)['\"]", src)
        if rev:
            revisions[rev.group(1)] = down.group(1) if down else None
    return alembic_head(revisions)


def node_id() -> str:
    out = aws(
        "ec2", "describe-instances",
        "--filters", f"Name=tag:Name,Values={PREFIX}", "Name=instance-state-name,Values=running",
        "--query", "Reservations[0].Instances[0].InstanceId", "--output", "text",
    )  # fmt: skip
    if not re.match(r"^i-[0-9a-f]+$", out):
        raise RuntimeError("staging has no running node. Bring it up first: make staging-up")
    return out


def on_node(instance: str) -> str:
    script = REMOTE % {"ns": NAMESPACE}
    with tempfile.TemporaryDirectory() as tmp:
        params = Path(tmp) / "params.json"
        params.write_text(json.dumps({"commands": [script]}), encoding="utf-8")
        cid = aws(
            "ssm", "send-command", "--instance-ids", instance,
            "--document-name", "AWS-RunShellScript",
            "--parameters", f"file://{params.as_posix()}",
            "--query", "Command.CommandId", "--output", "text",
        )  # fmt: skip
    status = "Pending"
    for _ in range(40):
        time.sleep(3)
        try:
            status = aws(
                "ssm", "get-command-invocation", "--command-id", cid, "--instance-id", instance,
                "--query", "Status", "--output", "text",
            )  # fmt: skip
        except RuntimeError:
            continue  # the invocation is not visible for the first second or two
        if status in ("Success", "Failed", "Cancelled", "TimedOut"):
            break
    return aws(
        "ssm", "get-command-invocation", "--command-id", cid, "--instance-id", instance,
        "--query", "StandardOutputContent", "--output", "text",
    )  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tag", help="expect this tag (default: the tag in staging's HelmRelease)")
    parser.add_argument("--no-record", action="store_true", help="judge only; write nothing")
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root")
    args = parser.parse_args(argv)

    os.environ.setdefault("AWS_PROFILE", "kaval")
    os.environ.setdefault("AWS_REGION", "ap-south-1")
    os.environ.setdefault("PYTHONUTF8", "1")

    try:
        tag = args.tag or staging_tag()
        if not TAG_RE.match(tag):
            raise RuntimeError(f"not a release tag: {tag!r}")
        digests = {s: ecr_digest(s, tag) for s in SERVICES}
        other = {}
        for svc in NOT_EXERCISED:
            try:
                other[svc] = ecr_digest(svc, tag)
            except RuntimeError:
                print(f"note: kaval/{svc}:{tag} is not in ECR (a build from before it existed)")
        commit = git("rev-parse", "--verify", f"{tag[4:]}^{{commit}}").strip() or None
        head = commit_head(commit) if commit else None
        print(
            f"expecting {tag} (commit {commit or 'unknown to this clone'}), "
            f"migration head {head or 'unknown'}"
        )
        observed = parse_observation(on_node(node_id()))
    except RuntimeError as exc:
        print(f"cannot run the smoke test: {exc}", file=sys.stderr)
        return 2

    checks = evaluate(observed, tag, digests, head)
    for c in checks:
        print(f"  {'PASS' if c.ok else 'FAIL'}  {c.name}: {c.detail}")
    if not all(c.ok for c in checks):
        print(f"\n{tag} did NOT pass staging. Nothing recorded.")
        return 1

    print(f"\n{tag} passed staging.")
    if args.no_record:
        return 0
    path = args.root / RECORD
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else None
    entry = build_entry(tag, commit, digests, other, checks, datetime.now(UTC))
    path.write_text(add_pass(existing, entry), encoding="utf-8", newline="\n")
    print(f"recorded in {RECORD}. Commit it through a pull request; promote.yml will check it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
