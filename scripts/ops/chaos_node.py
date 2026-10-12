"""Game day: lose the app server, and time how Kaval comes back (KAV-80, ADR-0046).

The other experiments run INSIDE the cluster as a Job. This one cannot: it ends the machine the
cluster, and Kaval itself, runs on. So it runs from the laptop, with the laptop's AWS login, and
the guard is in this file instead of in a Kubernetes switch:

  * it only ever picks the machine tagged Name=kaval-staging that belongs to the auto-scaling group
    `kaval-staging`, and refuses on anything else (prod's are named kaval-prod). The group name is
    the identity: the group passes only the Name and Project tags to its machines, so an Env tag
    cannot be relied on (the first live attempt, 2026-10-12, refused for expecting one);
  * you must type that machine's id back to start;
  * the machine is ended through the auto-scaling group WITHOUT lowering its wanted size, so the
    group replaces it by itself. That replacement is what is being tested.

What it measures, as seconds from the moment the machine is ended: the old one gone, a new one
running, Session Manager reachable, the cluster node Ready, every pod ready again, the gateway
healthy (which includes reaching the database server), and the incident rows written before the
loss still there. Nothing here repairs anything; the group does. Kaval cannot raise an alarm about
its own death, and the finding this run exists to put numbers on is how long nobody would know.

    python scripts/ops/chaos_node.py            # make chaos-node
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

GROUP = "kaval-staging"
NAME_TAG = "kaval-staging"
NAMESPACE = "kaval-staging"
MAX_SECONDS = 30 * 60
POLL_SECONDS = 15
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

EXIT_OK, EXIT_REFUSED, EXIT_TIMEOUT = 0, 2, 4


class Refused(RuntimeError):
    """The experiment must not start."""


@dataclass(frozen=True)
class Node:
    instance_id: str
    state: str   # pending | running | shutting-down | terminated ...
    group: str   # the auto-scaling group the machine belongs to
    name: str    # the Name tag


class Aws(Protocol):
    def nodes(self) -> list[Node]: ...
    def end(self, instance_id: str) -> None: ...
    def ssm_online(self, instance_id: str) -> bool: ...
    def on_node(self, instance_id: str, script: str) -> str: ...


def guard(node: Node | None) -> Node:
    """The machine to end, or `Refused`. Checks the group and the tag, not just the name."""
    if node is None:
        raise Refused("staging is not running (no machine tagged Name=kaval-staging). "
                      "Start it with: make staging-up")
    if node.group != GROUP or node.name != NAME_TAG:
        raise Refused(f"{node.instance_id} is in group {node.group!r} with Name={node.name!r}; "
                      f"only group {GROUP!r} with Name={NAME_TAG!r} may be ended")
    return node


def the_running_node(nodes: list[Node]) -> Node | None:
    running = [n for n in nodes if n.state == "running"]
    if len(running) > 1:
        raise Refused(f"{len(running)} staging machines are running; expected exactly one, "
                      "so not guessing which to end")
    return running[0] if running else None


# One script on the node, asked the same way before and after. Prints key=value lines; every value
# is a plain word or number so the parser cannot be fooled by anything the cluster prints.
PROBE = r"""
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
K=/usr/local/bin/kubectl
echo node_ready=$($K get nodes --no-headers 2>/dev/null | grep -c ' Ready')
$K get pods -n NAMESPACE --no-headers 2>/dev/null | awk '
  $3=="Completed" {next}
  {split($2,a,"/"); if ($3=="Running" && a[1]==a[2]) ok++; else bad++}
  END {print "pods_ready=" ok+0; print "pods_not_ready=" bad+0}'
IP=$($K get svc -n NAMESPACE -o \
  jsonpath='{.items[?(@.spec.ports[0].port==8000)].spec.clusterIP}' 2>/dev/null)
# /healthz is 503 on staging by design (it also checks a language model that staging does not
# have), so "healthy" here is: the gateway answers AND its database check is ok.
echo gateway=$(curl -s --max-time 5 http://$IP:8000/healthz 2>/dev/null | python3 -c '
import sys, json
try:
    print(200 if json.load(sys.stdin)["postgres"]["ok"] else 503)
except Exception:
    print(0)')
curl -s --max-time 8 "http://$IP:8000/v1/incidents?limit=200" 2>/dev/null | python3 -c '
import sys, json
try:
    items = json.load(sys.stdin)["items"]
    print("incidents=%d" % len(items))
    print("incident_newest=%s" % (items[0]["id"] if items else "none"))
except Exception:
    print("incidents=-1")
    print("incident_newest=unknown")'
NEWEST_CHECK
""".replace("NAMESPACE", NAMESPACE)

# Appended to the probe after the loss, with the id seen before it.
NEWEST_CHECK = (
    "echo newest_found=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "
    "http://$IP:8000/v1/incidents/{id} 2>/dev/null)"
)


def parse(output: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in output.splitlines():
        m = re.fullmatch(r"([a-z_]+)=([\w.-]*)", line.strip())
        if m:
            values[m.group(1)] = m.group(2)
    return values


def probe(aws: Aws, instance_id: str, newest: str | None = None) -> dict[str, str]:
    check = NEWEST_CHECK.replace("{id}", newest) if newest and UUID.match(newest) else "true"
    try:
        return parse(aws.on_node(instance_id, PROBE.replace("NEWEST_CHECK", check)))
    except Exception:  # noqa: BLE001 - a machine that is still booting answers nothing, not an error
        return {}


@dataclass
class Timeline:
    started_at: str = ""
    old_instance: str = ""
    new_instance: str = ""
    events: dict[str, float] = field(default_factory=dict)
    before: dict[str, str] = field(default_factory=dict)
    after: dict[str, str] = field(default_factory=dict)
    result: str = "running"

    def mark(self, name: str, elapsed: float) -> None:
        self.events.setdefault(name, round(elapsed, 1))


def run(
    aws: Aws, *, confirm: Callable[[Node], bool], max_seconds: float = MAX_SECONDS,
    poll_seconds: float = POLL_SECONDS, clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[int, Timeline]:
    timeline = Timeline(started_at=datetime.now(UTC).isoformat())
    node = guard(the_running_node(aws.nodes()))
    timeline.old_instance = node.instance_id

    before = probe(aws, node.instance_id)
    timeline.before = before
    if before.get("gateway") != "200":
        raise Refused("Kaval on staging is not healthy to begin with "
                      f"(gateway answered {before.get('gateway', 'nothing')!r}); fix that first, "
                      "or the run would measure nothing")
    pods_before = int(before.get("pods_ready", "0"))
    newest = before.get("incident_newest")
    incidents_before = int(before.get("incidents", "-1"))

    if not confirm(node):
        raise Refused("not confirmed")

    t0 = clock()
    aws.end(node.instance_id)
    timeline.mark("machine_ended", 0)
    while True:
        elapsed = clock() - t0
        if elapsed >= max_seconds:
            timeline.result = f"timeout after {int(max_seconds)} s: not fully back"
            return EXIT_TIMEOUT, timeline
        nodes = aws.nodes()
        old = next((n for n in nodes if n.instance_id == node.instance_id), None)
        if old is None or old.state != "running":
            timeline.mark("old_machine_gone", elapsed)
        fresh = next((n for n in nodes if n.instance_id != node.instance_id
                      and n.state == "running" and n.group == GROUP), None)
        if fresh is not None:
            timeline.new_instance = fresh.instance_id
            timeline.mark("new_machine_running", elapsed)
            online = aws.ssm_online(fresh.instance_id)
            # Stamp each step AFTER the call that saw it returns: a probe on a machine that is
            # still booting can block for a minute or more, and stamping at the top of the loop
            # would date every step to before it was seen (checked against the cluster's clocks
            # on the first live run, 2026-10-12).
            if online:
                timeline.mark("session_manager_reachable", clock() - t0)
                now = probe(aws, fresh.instance_id, newest)
                elapsed = clock() - t0
                timeline.after = now
                if int(now.get("node_ready", "0")) >= 1:
                    timeline.mark("cluster_node_ready", elapsed)
                if (int(now.get("pods_ready", "0")) >= pods_before
                        and now.get("pods_not_ready", "1") == "0"):
                    timeline.mark("all_pods_ready", elapsed)
                if now.get("gateway") == "200":
                    timeline.mark("gateway_healthy", elapsed)
                intact = (int(now.get("incidents", "-1")) >= incidents_before
                          and (newest in (None, "none", "unknown")
                               or now.get("newest_found") == "200"))
                if intact and "gateway_healthy" in timeline.events:
                    timeline.mark("data_intact", elapsed)
                if {"all_pods_ready", "gateway_healthy", "data_intact"} <= set(timeline.events):
                    timeline.result = "recovered: new machine, every pod ready, data intact"
                    return EXIT_OK, timeline
        sleep(poll_seconds)


class CliAws:
    """The real thing: the aws command line, with the laptop's login."""

    def _aws(self, *args: str) -> str:
        return subprocess.run(["aws", *args], check=True, capture_output=True, text=True,
                              encoding="utf-8").stdout

    def nodes(self) -> list[Node]:
        out = self._aws(
            "ec2", "describe-instances", "--filters", "Name=tag:Name,Values=kaval-staging",
            "--query",
            "Reservations[].Instances[].[InstanceId,State.Name,"
            "Tags[?Key=='aws:autoscaling:groupName']|[0].Value,"
            "Tags[?Key=='Name']|[0].Value]",
            "--output", "json")
        return [Node(i, s, g or "", n or "") for i, s, g, n in json.loads(out)]

    def end(self, instance_id: str) -> None:
        self._aws("autoscaling", "terminate-instance-in-auto-scaling-group",
                  "--instance-id", instance_id, "--no-should-decrement-desired-capacity")

    def ssm_online(self, instance_id: str) -> bool:
        out = self._aws(
            "ssm", "describe-instance-information", "--filters",
            f"Key=InstanceIds,Values={instance_id}", "--query",
            "InstanceInformationList[0].PingStatus", "--output", "text")
        return out.strip() == "Online"

    def on_node(self, instance_id: str, script: str) -> str:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({"commands": [script]}, f)
            params = Path(f.name)
        try:
            command_id = self._aws(
                "ssm", "send-command", "--instance-ids", instance_id, "--document-name",
                "AWS-RunShellScript", "--parameters", f"file://{params.as_posix()}",
                "--query", "Command.CommandId", "--output", "text").strip()
        finally:
            params.unlink(missing_ok=True)
        for _ in range(30):
            time.sleep(3)
            try:
                status = self._aws("ssm", "get-command-invocation", "--command-id", command_id,
                                   "--instance-id", instance_id, "--query", "Status",
                                   "--output", "text").strip()
            except subprocess.CalledProcessError:
                continue  # the invocation is not visible for a moment after sending
            if status in {"Success", "Failed", "Cancelled", "TimedOut"}:
                break
        return self._aws("ssm", "get-command-invocation", "--command-id", command_id,
                         "--instance-id", instance_id, "--query", "StandardOutputContent",
                         "--output", "text")


def _confirm(node: Node) -> bool:
    if os.environ.get("ASSUME_YES") == "1":
        return True
    print(f"\nThis ENDS staging's app machine {node.instance_id} (group {node.group}).")
    print("Kaval on staging goes down with it and cannot alarm about that. The group should "
          "replace the machine by itself; this times how long.")
    typed = input(f"Type the machine id ({node.instance_id}) to go ahead: ")
    return typed.strip() == node.instance_id


def main() -> int:
    os.environ.setdefault("AWS_PROFILE", "kaval")
    os.environ.setdefault("AWS_REGION", "ap-south-1")
    try:
        code, timeline = run(CliAws(), confirm=_confirm)
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    print(json.dumps({"experiment": "node", "started_at": timeline.started_at,
                      "old_instance": timeline.old_instance, "new_instance": timeline.new_instance,
                      "events_seconds": timeline.events, "before": timeline.before,
                      "after": timeline.after, "result": timeline.result}, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
