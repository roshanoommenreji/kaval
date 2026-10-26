"""Smoke-test production after a bring-up. Read-only: it records and changes nothing (KAV-90).

`staging_smoke.py` proves a build runs on staging and writes the pass that lets it be promoted.
Nothing yet proved the same of production once it has come up from parked, which was one of the
Phase 6 exit checks. This runs the same five checks against production's own namespace and adds
two that only production can answer:

  6. the images prod runs are the ones that passed staging: the digest ECR holds for prod's tag is
     the digest on deploy/promotion/passed-staging.json. With check 3 (what runs equals what ECR
     holds) that is "nothing reaches prod without passing staging", read back from the live system;
  7. the server's own timers are set: the nightly dump (19:30 UTC, 01:00 IST), the cost reader's
     copy step and the heartbeat. A prod that is up but whose timers did not start would pass 1 to
     6 and then quietly stop being backed up.

It also prints two notes that are not gates: the memory still available and what the model server
says. The model is allowed minutes to load, and available memory dips to about 800 MB while it is
loaded (ADR-0055), so neither is a reason to fail.

    python scripts/release/prod_smoke.py                      # the tag in prod's own files
    python scripts/release/prod_smoke.py --tag sha-xxxxxxx    # a deliberate fail

Exit 0 passes, 1 fails, 2 cannot run (no AWS, production is parked, the files disagree). The server
is reached over Session Manager (no SSH, no open port); only `kubectl get`, `curl` reads,
`systemctl is-active` and `free` run on it.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import staging_smoke as base
from promote import current_prod_tag, find_pass
from staging_smoke import Check

ROOT = base.ROOT
NAMESPACE = "kaval-prod"
PREFIX = "kaval-prod"  # the node's Name tag, set by the node module's name_prefix
RECORD = base.RECORD

# The server's timers (infra/modules/node/user_data.sh.tftpl) and the one time that matters most:
# the nightly dump, set for 19:30 UTC so it runs at 01:00 IST, before the 02:00 IST automatic stop.
TIMERS = ("kaval-db-backup", "kaval-cost-pull", "kaval-heartbeat")
DUMP_TIMER = "kaval-db-backup"
DUMP_AT = "19:30:00"

# `%` is left out of everything added here: the staging snippet is filled in with `%`.
_EXTRA = r"""
for t in kaval-db-backup kaval-cost-pull kaval-heartbeat; do
  echo "##timer $t"
  systemctl is-active $t.timer
  systemctl show $t.timer -p TimersCalendar --value
done
echo '##memory'
free -m | awk '/^Mem:/ {print $7}'
echo '##end'
"""
REMOTE = base.REMOTE.replace("echo '##end'", _EXTRA.strip("\n"))


# ── pure parts ───────────────────────────────────────────────────────────────────────────


def parse_extras(text: str) -> dict[str, Any]:
    """The part of the node's output that staging's parser does not read: each timer's state and
    calendar, and the available memory in MB. Never raises on odd output."""
    timers: dict[str, tuple[str, str]] = {}
    memory: int | None = None
    current: str | None = None
    lines: list[str] = []

    def close() -> None:
        nonlocal memory
        if current is None:
            return
        if current == "memory":
            first = lines[0] if lines else ""
            memory = int(first) if first.isdigit() else None
        elif current.startswith("timer "):
            state = lines[0] if lines else ""
            calendar = lines[1] if len(lines) > 1 else ""
            timers[current[6:]] = (state, calendar)

    for raw in text.splitlines():
        if raw.startswith("##"):
            close()
            current, lines = raw[2:].strip(), []
        elif raw.strip() and current is not None:
            lines.append(raw.strip())
    close()
    return {"timers": timers, "memory_mb": memory}


def check_record(record_text: str | None, tag: str, digests: dict[str, str]) -> Check:
    name = "these exact images passed staging"
    entry = find_pass(record_text, tag)
    if entry is None:
        return Check(name, False, f"{tag} is not on {RECORD}")
    images = entry.get("images", {})
    differ = [s for s in base.SERVICES if images.get(s) != digests.get(s)]
    if differ:
        return Check(
            name,
            False,
            f"{tag} is on the record, but ECR now holds different bytes for {', '.join(differ)}",
        )
    return Check(name, True, f"{tag} passed staging {entry.get('passed_at', '(no time)')}")


def check_timers(timers: dict[str, tuple[str, str]]) -> Check:
    name = "the server's timers are set"
    bad = [
        f"{t} is {timers.get(t, ('missing', ''))[0] or 'missing'}"
        for t in TIMERS
        if timers.get(t, ("", ""))[0] != "active"
    ]
    dump = timers.get(DUMP_TIMER, ("", ""))[1]
    if DUMP_AT not in dump:
        bad.append(f"{DUMP_TIMER} is not set for {DUMP_AT} UTC (it says {dump or 'nothing'})")
    return Check(
        name, not bad, "; ".join(bad) or f"dump at {DUMP_AT} UTC; all {len(TIMERS)} active"
    )


def notes(observed: dict[str, Any], extras: dict[str, Any]) -> list[str]:
    health = observed.get("health") or {}
    model = health.get("ollama") or {}
    out = [
        "model server: "
        + (
            f"{'ready' if model.get('ok') else 'not ready'} ({model.get('detail', 'no answer')})"
            + ("" if model.get("ok") else ", given time to load; not gated on")
        )
    ]
    memory = extras.get("memory_mb")
    out.append(
        f"memory available: {memory} MB (about 800 MB is normal while the model is loaded)"
        if memory is not None
        else "memory available: not read"
    )
    return out


def evaluate_prod(
    observed: dict[str, Any],
    extras: dict[str, Any],
    tag: str,
    digests: dict[str, str],
    expected_head: str | None,
    record_text: str | None,
) -> list[Check]:
    checks = base.evaluate(observed, tag, digests, expected_head)
    checks.append(check_record(record_text, tag, digests))
    checks.append(check_timers(extras["timers"]))
    return checks


# ── the parts that touch AWS and Git ─────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--tag", help="expect this tag (default: the tag in prod's own files)")
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root")
    args = parser.parse_args(argv)

    os.environ.setdefault("AWS_PROFILE", "kaval")
    os.environ.setdefault("AWS_REGION", "ap-south-1")
    os.environ.setdefault("PYTHONUTF8", "1")

    try:
        tag = args.tag or current_prod_tag(args.root)
        if not base.TAG_RE.match(tag):
            raise RuntimeError(f"not a release tag: {tag!r}")
        digests = {s: base.ecr_digest(s, tag) for s in base.SERVICES}
        commit = base.git("rev-parse", "--verify", f"{tag[4:]}^{{commit}}").strip() or None
        head = base.commit_head(commit) if commit else None
        record = args.root / RECORD
        record_text = record.read_text(encoding="utf-8") if record.exists() else None
        print(
            f"expecting {tag} (commit {commit or 'unknown to this clone'}), "
            f"migration head {head or 'unknown'}"
        )
        node = base.node_id(PREFIX, "see docs/runbooks/prod-rollout.md")
        text = base.on_node(node, REMOTE % {"ns": NAMESPACE})
    except RuntimeError as exc:
        print(f"cannot run the smoke test: {exc}", file=sys.stderr)
        return 2

    observed = base.parse_observation(text)
    extras = parse_extras(text)
    checks = evaluate_prod(observed, extras, tag, digests, head, record_text)
    for c in checks:
        print(f"  {'PASS' if c.ok else 'FAIL'}  {c.name}: {c.detail}")
    for line in notes(observed, extras):
        print(f"  note  {line}")
    if not all(c.ok for c in checks):
        print(f"\nproduction running {tag} did NOT pass.")
        return 1
    print(f"\nproduction running {tag} passed. Nothing was recorded or changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
