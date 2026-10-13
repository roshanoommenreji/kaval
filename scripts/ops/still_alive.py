"""Tell the outside "still alive" check that a PARKED production is fine (KAV-83, ADR-0052).

Run every five minutes by `.github/workflows/still-alive.yml`, from GitHub and not from AWS, so it
keeps working when everything in AWS is parked or broken.

Production sends its own "still alive" pings while it runs (the Prometheus poller,
`kaval_collector.heartbeat`). While it is parked nothing runs, so nothing would ping, and the
outside service would raise a false alarm every parked day. This script covers exactly that gap and
nothing else:

    app-server group wants 0 servers and has none  -> parked  -> send the ping for it
    the group wants a server, or still has one      -> running -> send nothing: the poller speaks
    the group cannot be read, or is not there       -> unknown -> send nothing, exit 2

Sending nothing when it is unsure is the safe direction: the outside service then alarms after its
grace period, which is loud. The unsafe direction would be pinging for a production that is running
but dead, which would hide exactly what the check exists to find. So a ping here needs a positive
"parked", never merely an absence of "running".

The address comes from the environment (a repository secret), is never printed, and is checked and
sent by the same code the poller uses.

    HEARTBEAT_URL=... python scripts/ops/still_alive.py <auto-scaling-group>
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "collector"))
from kaval_collector import heartbeat  # noqa: E402 - the path above has to be set first

State = Literal["parked", "running", "unknown"]

# An instance in one of these has already gone or is going: it does not count as running.
GONE = {"Terminated", "Terminating:Wait", "Terminating:Proceed"}


def state_of(answer: object, group: str) -> tuple[State, str]:
    """`aws autoscaling describe-auto-scaling-groups` answer -> (state, one plain sentence)."""
    groups = answer.get("AutoScalingGroups") if isinstance(answer, Mapping) else None
    if not isinstance(groups, list):
        return "unknown", "the answer was not a list of groups"
    mine = [g for g in groups if isinstance(g, Mapping) and g.get("AutoScalingGroupName") == group]
    if len(mine) != 1:
        return "unknown", f"expected exactly one group called {group}, found {len(mine)}"
    desired = mine[0].get("DesiredCapacity")
    instances = mine[0].get("Instances")
    if not isinstance(desired, int) or isinstance(desired, bool) or not isinstance(instances, list):
        return "unknown", "the group's size or instance list was missing"
    live = [i for i in instances
            if isinstance(i, Mapping) and i.get("LifecycleState") not in GONE]
    if desired == 0 and not live:
        return "parked", "the group wants 0 servers and has none"
    if desired > 0:
        return "running", f"the group wants {desired} server(s) and has {len(live)}"
    return "running", f"the group wants 0 but {len(live)} server(s) have not gone yet"


def describe(group: str) -> object:
    out = subprocess.run(
        ["aws", "autoscaling", "describe-auto-scaling-groups",
         "--auto-scaling-group-names", group, "--output", "json"],
        check=True, capture_output=True, text=True, encoding="utf-8").stdout
    return json.loads(out) if out.strip() else None


def main(
    argv: list[str], *, environ: Mapping[str, str] | None = None,
    reader: Callable[[str], object] = describe,
    sender: Callable[[str], str | None] = heartbeat.send,
) -> int:
    """Exit 0: decided (pinged or deliberately not). 1: parked but the ping failed. 2: could not
    decide, or a bad setting. Nothing printed contains the address."""
    if len(argv) != 2:
        print("usage: still_alive.py <auto-scaling-group>")
        return 2
    group = argv[1]
    try:
        url = heartbeat.check_url((environ if environ is not None else os.environ)
                                  .get("HEARTBEAT_URL", ""))
    except ValueError as exc:
        print(f"HEARTBEAT_URL: {exc}")
        return 2
    if not url:
        print("HEARTBEAT_URL is not set: nothing to do")
        return 2
    try:
        answer = reader(group)
    except (subprocess.CalledProcessError, OSError, json.JSONDecodeError) as exc:
        print(f"could not read the group ({type(exc).__name__}): no ping sent")
        return 2
    state, why = state_of(answer, group)
    print(f"{group}: {state} ({why})")
    if state == "unknown":
        print("no ping sent: the outside check will raise the alarm if this keeps happening")
        return 2
    if state == "running":
        print("no ping sent: while it runs, its own poller does the pinging")
        return 0
    failure = sender(url)
    if failure:
        print(f"ping failed: {failure}")
        return 1
    print("pinged for the parked server")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
