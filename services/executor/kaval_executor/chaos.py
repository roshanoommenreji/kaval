"""Chaos runner: causes one failure on purpose, on staging, and times what follows (KAV-79,
ADR-0044).

This is the BREAKER. It is not the fixer: it runs under its own ServiceAccount
(`<release>-chaos-runner`) whose only grant is in the demo namespace, and it never talks to the
database or the policy engine. Whether Kaval notices and repairs the damage is exactly what the
experiment measures, so the runner only creates the failure, watches the result and cleans up.

It lives in the executor image (no new image to build, scan and pin) but is a different command
and a different identity; the executor Deployment never runs it.

Three things stop it, in this order of strength:
  1. The switch. A ConfigMap that the chart renders as "false" on every deploy and that a person
     turns on for a game day. The runner refuses to start with it off and aborts if it is turned
     off mid-run.
  2. The environment. It refuses unless the chart says this is `local` or `staging`. In prod the
     chart renders no runner at all (`chaos.enabled` is false there); this is the second lock.
  3. The clock. The CronJob's `activeDeadlineSeconds` ends the pod whatever the code is doing,
     and `MAX_SECONDS` ends the experiment earlier, from the inside.

    python -m kaval_executor.chaos oom      # as the chaos CronJob runs it
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from kubernetes import client
from kubernetes.client.exceptions import ApiException
from kubernetes.utils import parse_quantity

from kaval_executor import k8s

ALLOWED_ENVIRONMENTS = frozenset({"local", "staging"})
VICTIM_LABEL = "kaval.dev/chaos"
# The same pinned base image the services are built on; public, so the demo namespace needs no
# image-pull Secret (the ECR one exists only in Kaval's own namespace).
VICTIM_IMAGE = ("python:3.11-slim-bookworm@sha256:"
                "0a310eeecf4e1f5a0743f9a6520c90c88d089c903ca5fd283f501e3a805f5f89")
# A program that really needs ~100 MiB, started with a 64 MiB allowance.
VICTIM_NEEDS_MIB = 96
VICTIM_LIMIT = "64Mi"
VICTIM_NAME = "chaos-oomer"
STABLE_SECONDS = 60
MAX_SECONDS = 20 * 60  # inside the CronJob's 25-minute hard limit; long enough for a person to answer
POLL_SECONDS = 10

EXIT_OK, EXIT_REFUSED, EXIT_ABORTED, EXIT_TIMEOUT = 0, 2, 3, 4


class Refused(RuntimeError):
    """The experiment must not start."""


def guard(environment: str, switch_on: bool) -> None:
    """Raise `Refused` unless this environment may run chaos and the switch is on."""
    if environment not in ALLOWED_ENVIRONMENTS:
        raise Refused(f"environment {environment!r} may not run chaos "
                      f"(allowed: {', '.join(sorted(ALLOWED_ENVIRONMENTS))})")
    if not switch_on:
        raise Refused("the chaos switch is off; turn it on for a game day (make chaos-run does)")


def victim_manifest(namespace: str, needs_mib: int = VICTIM_NEEDS_MIB) -> dict[str, Any]:
    """A Deployment whose one container needs more memory than its limit allows. It touches
    every page of what it allocates (an untouched allocation costs no memory), then sleeps."""
    program = (
        f"b = bytearray({needs_mib} * 1024 * 1024)\n"
        "for i in range(0, len(b), 4096):\n"
        "    b[i] = 1\n"
        "print('holding memory', flush=True)\n"
        "import time\n"
        "time.sleep(86400)\n"
    )
    labels = {"app": VICTIM_NAME, VICTIM_LABEL: "true"}
    return {
        "apiVersion": "apps/v1", "kind": "Deployment",
        "metadata": {"name": VICTIM_NAME, "namespace": namespace, "labels": labels},
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"app": VICTIM_NAME}},
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "automountServiceAccountToken": False,
                    "containers": [{
                        "name": "app", "image": VICTIM_IMAGE,
                        "command": ["python", "-c", program],
                        "resources": {"requests": {"memory": "32Mi", "cpu": "10m"},
                                      "limits": {"memory": VICTIM_LIMIT, "cpu": "100m"}},
                        "securityContext": {"runAsNonRoot": True, "runAsUser": 10001,
                                            "allowPrivilegeEscalation": False,
                                            "capabilities": {"drop": ["ALL"]}},
                    }],
                },
            },
        },
    }


@dataclass
class Snapshot:
    limit_mib: int | None
    ready: bool
    oom_killed: bool
    restarts: int


class Cluster(Protocol):
    def switch_on(self) -> bool: ...
    def victim_exists(self) -> bool: ...
    def create_victim(self) -> None: ...
    def snapshot(self) -> Snapshot: ...
    def delete_victim(self) -> None: ...


@dataclass
class Timeline:
    """What happened and when, as seconds from the start. The post-mortem copies this."""
    experiment: str
    started_at: str = ""
    events: dict[str, float] = field(default_factory=dict)
    result: str = "running"

    def mark(self, name: str, elapsed: float) -> None:
        self.events.setdefault(name, round(elapsed, 1))


def run_oom(
    cluster: Cluster, *, max_seconds: float = MAX_SECONDS, stable_seconds: float = STABLE_SECONDS,
    poll_seconds: float = POLL_SECONDS, clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[int, Timeline]:
    """Create the victim, then watch until Kaval's fix holds, the switch goes off, or time runs
    out. The victim is always deleted on the way out."""
    timeline = Timeline("oom", started_at=datetime.now(UTC).isoformat())
    if cluster.victim_exists():
        raise Refused(f"{VICTIM_NAME} already exists; run `make chaos-stop` first")
    t0 = clock()
    cluster.create_victim()
    timeline.mark("victim_created", 0)
    initial_limit: int | None = None
    stable_since: float | None = None
    try:
        while True:
            elapsed = clock() - t0
            if not cluster.switch_on():
                timeline.result = "aborted: switch turned off"
                return EXIT_ABORTED, timeline
            if elapsed >= max_seconds:
                timeline.result = f"timeout after {int(max_seconds)} s: not recovered"
                return EXIT_TIMEOUT, timeline
            snap = cluster.snapshot()
            if snap.limit_mib is not None and initial_limit is None:
                initial_limit = snap.limit_mib
            if snap.oom_killed:
                timeline.mark("first_oom_killed", elapsed)
            raised = (initial_limit is not None and snap.limit_mib is not None
                      and snap.limit_mib > initial_limit)
            if raised:
                timeline.mark("limit_raised", elapsed)
            if raised and snap.ready:
                stable_since = stable_since if stable_since is not None else elapsed
                if elapsed - stable_since >= stable_seconds:
                    timeline.mark("recovered", stable_since)
                    timeline.result = "recovered: limit raised and the program stayed up"
                    return EXIT_OK, timeline
            else:
                stable_since = None
            sleep(poll_seconds)
    finally:
        cluster.delete_victim()
        timeline.mark("victim_deleted", clock() - t0)


class KubeCluster:
    """The real thing, behind the `Cluster` protocol so the loop above is testable."""

    def __init__(self, namespace: str, release_namespace: str, switch_name: str) -> None:
        self.namespace = namespace
        self.release_namespace = release_namespace
        self.switch_name = switch_name
        self.apps = client.AppsV1Api()
        self.core = client.CoreV1Api()

    def switch_on(self) -> bool:
        cm = self.core.read_namespaced_config_map(self.switch_name, self.release_namespace)
        return str((cm.data or {}).get("enabled", "false")).lower() == "true"

    def victim_exists(self) -> bool:
        try:
            self.apps.read_namespaced_deployment(VICTIM_NAME, self.namespace)
        except ApiException as exc:
            if exc.status == 404:
                return False
            raise
        return True

    def create_victim(self) -> None:
        # The client accepts the plain-dict form of a manifest; its type hints only name the model.
        self.apps.create_namespaced_deployment(
            self.namespace, victim_manifest(self.namespace))  # type: ignore[arg-type]

    def snapshot(self) -> Snapshot:
        deployment = self.apps.read_namespaced_deployment(VICTIM_NAME, self.namespace)
        raw: Any = None
        for c in (deployment.spec.template.spec.containers  # type: ignore[union-attr]
                  if deployment.spec else []):
            raw = ((c.resources.limits if c.resources else None) or {}).get("memory")
            break
        limit_mib = (int(parse_quantity(raw)) // k8s.MIB  # type: ignore[no-untyped-call]
                     if raw else None)
        status = deployment.status
        pods = self.core.list_namespaced_pod(
            self.namespace, label_selector=f"app={VICTIM_NAME}").items
        oom = False
        restarts = 0
        for pod in pods:
            for cs in (pod.status.container_statuses or []) if pod.status else []:
                restarts += int(cs.restart_count or 0)
                last = cs.last_state.terminated if cs.last_state else None
                if last is not None and last.reason == "OOMKilled":
                    oom = True
        ready = bool(status and (status.ready_replicas or 0) >= 1
                     and (status.unavailable_replicas or 0) == 0)
        return Snapshot(limit_mib=limit_mib, ready=ready, oom_killed=oom, restarts=restarts)

    def delete_victim(self) -> None:
        try:
            self.apps.delete_namespaced_deployment(VICTIM_NAME, self.namespace)
        except ApiException as exc:
            if exc.status != 404:
                raise


EXPERIMENTS: dict[str, Callable[..., tuple[int, Timeline]]] = {"oom": run_oom}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    parser.add_argument("experiment", choices=sorted(EXPERIMENTS))
    args = parser.parse_args(argv)

    environment = os.environ.get("CHAOS_ENVIRONMENT", "")
    namespace = os.environ.get("CHAOS_TARGET_NAMESPACE", "")
    release_namespace = os.environ.get("POD_NAMESPACE", "")
    switch_name = os.environ.get("CHAOS_SWITCH", "")
    if not (namespace and release_namespace and switch_name):
        print("refused: CHAOS_TARGET_NAMESPACE, POD_NAMESPACE and CHAOS_SWITCH must be set",
              file=sys.stderr)
        return EXIT_REFUSED
    try:
        k8s.load_config()
        cluster = KubeCluster(namespace, release_namespace, switch_name)
        guard(environment, cluster.switch_on())
        code, timeline = EXPERIMENTS[args.experiment](cluster)
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    print(json.dumps({"experiment": timeline.experiment, "started_at": timeline.started_at,
                      "events_seconds": timeline.events, "result": timeline.result}))
    return code


if __name__ == "__main__":
    sys.exit(main())
