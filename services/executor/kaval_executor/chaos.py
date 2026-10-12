"""Chaos runner: causes one failure on purpose, on staging, and times what follows (KAV-79,
ADR-0044).

This is the BREAKER. It is not the fixer: it runs under its own ServiceAccount
(`<release>-chaos-runner`) whose only grant is in the demo namespace, and it never talks to the
database or the policy engine. Whether Kaval notices and repairs the damage is exactly what the
experiment measures, so the runner only creates the failure, watches the result and cleans up.

It lives in the executor image (no new image to build, scan and pin) but is a different command
and a different identity; the executor Deployment never runs it.

Four experiments in all; the last two (KAV-81, ADR-0047) are different in kind: there is no fix to
test, because the right outcome is that Kaval tells a person and proposes nothing.

Three things stop it, in this order of strength:
  1. The switch. A ConfigMap that the chart renders as "false" on every deploy and that a person
     turns on for a game day. The runner refuses to start with it off and aborts if it is turned
     off mid-run.
  2. The environment. It refuses unless the chart says this is `local` or `staging`. In prod the
     chart renders no runner at all (`chaos.enabled` is false there); this is the second lock.
  3. The clock. The CronJob's `activeDeadlineSeconds` ends the pod whatever the code is doing,
     and `MAX_SECONDS` ends the experiment earlier, from the inside.

Two experiments (KAV-79, KAV-80), each with its own victim Deployment in the demo namespace:
  oom         a program that needs more memory than its limit allows. Fix: raise the limit.
  badrelease  a healthy program, then a new version that crashes at start-up. Fix: roll back.
              Delivering the new version is a `patch`, which the runner may do to this one
              named Deployment only (RBAC `resourceNames`), never to anything else.

  slow        a web program that answers fast, then slowly. Fix: none. A person is told.
  disk        a program that writes a bounded 3 GiB file. Fix: none. A person is told.
              (Both only hold the condition for a while and clean up; whether Kaval escalated and
              proposed nothing is read afterwards from the database: `make chaos-check`.)

    python -m kaval_executor.chaos oom          # as the chaos CronJob runs it
    python -m kaval_executor.chaos badrelease
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
RELEASE_VICTIM_NAME = "chaos-badrelease"
SLOW_VICTIM_NAME = "chaos-slowpoke"
DISK_VICTIM_NAME = "chaos-diskfill"
# The slow program answers fast for this long after it starts, then takes SLOW_BY_SECONDS each.
SLOW_FAST_FOR_SECONDS = 120
SLOW_BY_SECONDS = 3
SLOW_PORT = 8080
# The disk program writes this much and no more, into a volume the kubelet caps at the limit below
# (it evicts the pod above it). Never the real disk: 3 GiB of the node's 20 GB, deleted on exit.
DISK_FILL_MIB = 3072
DISK_LIMIT = "4Gi"
# How long an escalate-only condition is held after the program is ready: enough for Kaval to
# notice, open the incident and tell a person, and for a person to try (and fail) to approve a fix.
HOLD_SECONDS = 12 * 60
STABLE_SECONDS = 60
# How long the good first version gets to come up before the experiment gives up.
HEALTHY_SECONDS = 5 * 60
# Inside the CronJob's 25-minute hard limit, and long enough for a person to approve the fix.
MAX_SECONDS = 20 * 60
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


def release_manifest(namespace: str, release: str) -> dict[str, Any]:
    """The release victim at one version. `v1` runs fine; `v2` is the bad release: it prints a
    configuration error and exits at once, so every start is a crash. The version is a label on
    the pod template, so the Deployment's rollout history has two real, different revisions."""
    if release == "v1":
        program = ("import time\nprint('release v1 running', flush=True)\ntime.sleep(86400)\n")
    else:
        program = ("import sys\nprint('release ' + sys.argv[1] + ': configuration error, "
                   "cannot start', flush=True)\nsys.exit(1)\n")
    labels = {"app": RELEASE_VICTIM_NAME, VICTIM_LABEL: "true"}
    return {
        "apiVersion": "apps/v1", "kind": "Deployment",
        "metadata": {"name": RELEASE_VICTIM_NAME, "namespace": namespace, "labels": labels},
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"app": RELEASE_VICTIM_NAME}},
            "template": {
                "metadata": {"labels": {**labels, "release": release}},
                "spec": {
                    "automountServiceAccountToken": False,
                    "containers": [{
                        "name": "app", "image": VICTIM_IMAGE,
                        "command": ["python", "-c", program, release],
                        "resources": {"requests": {"memory": "32Mi", "cpu": "10m"},
                                      "limits": {"memory": "128Mi", "cpu": "100m"}},
                        "securityContext": {"runAsNonRoot": True, "runAsUser": 10001,
                                            "allowPrivilegeEscalation": False,
                                            "capabilities": {"drop": ["ALL"]}},
                    }],
                },
            },
        },
    }


def bad_release_patch(namespace: str) -> dict[str, Any]:
    """The change that makes the new version: the pod template of `v2`, as a merge patch."""
    template = release_manifest(namespace, "v2")["spec"]["template"]
    return {"spec": {"template": {
        "metadata": {"labels": {"release": "v2"}},
        "spec": {"containers": [{"name": "app",
                                 "command": template["spec"]["containers"][0]["command"]}]},
    }}}


@dataclass
class ReleaseSnapshot:
    release: str | None  # the `release` label on the Deployment's pod template: "v1" or "v2"
    rolled_out: bool     # every replica is the latest template, ready, none unavailable
    crashing: bool       # a pod of the victim has ended with a non-zero exit


class ReleaseCluster(Protocol):
    def switch_on(self) -> bool: ...
    def victim_exists(self) -> bool: ...
    def create_victim(self) -> None: ...
    def deliver_bad_release(self) -> None: ...
    def release_snapshot(self) -> ReleaseSnapshot: ...
    def delete_victim(self) -> None: ...


def run_badrelease(
    cluster: ReleaseCluster, *, max_seconds: float = MAX_SECONDS,
    stable_seconds: float = STABLE_SECONDS, healthy_seconds: float = HEALTHY_SECONDS,
    poll_seconds: float = POLL_SECONDS, clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[int, Timeline]:
    """Start a healthy v1, ship a v2 that crashes, then watch until Kaval's rollback holds, the
    switch goes off, or time runs out. The victim is always deleted on the way out."""
    timeline = Timeline("badrelease", started_at=datetime.now(UTC).isoformat())
    if cluster.victim_exists():
        raise Refused(f"{RELEASE_VICTIM_NAME} already exists; run `make chaos-stop` first")
    t0 = clock()
    cluster.create_victim()
    timeline.mark("victim_created", 0)
    delivered = False
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
            if not delivered and elapsed >= healthy_seconds:
                timeline.result = f"v1 did not come up within {int(healthy_seconds)} s"
                return EXIT_TIMEOUT, timeline
            snap = cluster.release_snapshot()
            if not delivered:
                if snap.release == "v1" and snap.rolled_out:
                    timeline.mark("v1_healthy", elapsed)
                    cluster.deliver_bad_release()
                    delivered = True
                    timeline.mark("bad_release_delivered", clock() - t0)
            else:
                if snap.crashing:
                    timeline.mark("first_crash", elapsed)
                back = snap.release == "v1"
                if back:
                    timeline.mark("rolled_back", elapsed)
                if back and snap.rolled_out:
                    stable_since = stable_since if stable_since is not None else elapsed
                    if elapsed - stable_since >= stable_seconds:
                        timeline.mark("recovered", stable_since)
                        timeline.result = "recovered: rolled back and the program stayed up"
                        return EXIT_OK, timeline
                else:
                    stable_since = None
            sleep(poll_seconds)
    finally:
        cluster.delete_victim()
        timeline.mark("victim_deleted", clock() - t0)


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


def _victim_deployment(name: str, program: str, container_extra: dict[str, Any],
                       pod_extra: dict[str, Any] | None = None) -> dict[str, Any]:
    labels = {"app": name, VICTIM_LABEL: "true"}
    container: dict[str, Any] = {
        "name": "app", "image": VICTIM_IMAGE, "command": ["python", "-c", program],
        "securityContext": {"runAsNonRoot": True, "runAsUser": 10001,
                            "allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
        **container_extra,
    }
    return {
        "apiVersion": "apps/v1", "kind": "Deployment",
        "metadata": {"name": name, "labels": labels},
        "spec": {
            "replicas": 1, "selector": {"matchLabels": {"app": name}},
            "template": {"metadata": {"labels": labels}, "spec": {
                "automountServiceAccountToken": False, "containers": [container],
                **(pod_extra or {}),
            }},
        },
    }


def slowpoke_manifest(namespace: str) -> dict[str, Any]:
    """A small web program that answers at once for `SLOW_FAST_FOR_SECONDS` after it starts and then
    takes `SLOW_BY_SECONDS` to answer every request, for good. It stays up and Ready the whole time
    (a plain connection test, not a request), so the only thing wrong with it is that it is slow."""
    program = (
        "import http.server, time\n"
        "T0 = time.monotonic()\n"
        "class H(http.server.BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        f"        if time.monotonic() - T0 > {SLOW_FAST_FOR_SECONDS}:\n"
        f"            time.sleep({SLOW_BY_SECONDS})\n"
        "        self.send_response(200)\n"
        "        self.end_headers()\n"
        "        self.wfile.write(b'ok')\n"
        "    def log_message(self, *a):\n"
        "        pass\n"
        f"http.server.ThreadingHTTPServer(('', {SLOW_PORT}), H).serve_forever()\n"
    )
    manifest = _victim_deployment(SLOW_VICTIM_NAME, program, {
        "ports": [{"containerPort": SLOW_PORT}],
        "readinessProbe": {"tcpSocket": {"port": SLOW_PORT}, "periodSeconds": 5},
        "resources": {"requests": {"memory": "32Mi", "cpu": "10m"},
                      "limits": {"memory": "96Mi", "cpu": "200m"}},
    })
    manifest["metadata"]["namespace"] = namespace
    return manifest


def slowpoke_service(namespace: str) -> dict[str, Any]:
    return {
        "apiVersion": "v1", "kind": "Service",
        "metadata": {"name": SLOW_VICTIM_NAME, "namespace": namespace,
                     "labels": {VICTIM_LABEL: "true"}},
        "spec": {"selector": {"app": SLOW_VICTIM_NAME},
                 "ports": [{"port": SLOW_PORT, "targetPort": SLOW_PORT}]},
    }


def diskfill_manifest(namespace: str, fill_mib: int = DISK_FILL_MIB) -> dict[str, Any]:
    """A program that writes `fill_mib` MiB into its own volume and then sleeps. The volume is
    capped (`sizeLimit`) and so is the container (`ephemeral-storage`), so it cannot write more than
    the cap even if its code were wrong; the file is gone when the Deployment is deleted."""
    program = (
        "import os, time\n"
        "chunk = bytes(1024 * 1024)\n"
        "with open('/fill/blob', 'wb') as f:\n"
        f"    for _ in range({fill_mib}):\n"
        "        f.write(chunk)\n"
        "    f.flush()\n"
        "    os.fsync(f.fileno())\n"
        "open('/fill/done', 'w').write('1')\n"
        f"print('wrote {fill_mib} MiB', flush=True)\n"
        "time.sleep(86400)\n"
    )
    manifest = _victim_deployment(DISK_VICTIM_NAME, program, {
        "volumeMounts": [{"name": "fill", "mountPath": "/fill"}],
        "readinessProbe": {"exec": {"command": ["test", "-f", "/fill/done"]}, "periodSeconds": 5},
        "resources": {"requests": {"memory": "32Mi", "cpu": "10m", "ephemeral-storage": "100Mi"},
                      "limits": {"memory": "128Mi", "cpu": "200m",
                                 "ephemeral-storage": DISK_LIMIT}},
    }, {
        "volumes": [{"name": "fill", "emptyDir": {"sizeLimit": DISK_LIMIT}}],
        "securityContext": {"fsGroup": 10001},
    })
    manifest["metadata"]["namespace"] = namespace
    return manifest


class HoldCluster(Protocol):
    def switch_on(self) -> bool: ...
    def victim_exists(self) -> bool: ...
    def create_victim(self) -> None: ...
    def victim_ready(self) -> bool: ...
    def delete_victim(self) -> None: ...


def run_hold(
    experiment: str, cluster: HoldCluster, *, hold_seconds: float = HOLD_SECONDS,
    ready_seconds: float = HEALTHY_SECONDS, max_seconds: float = MAX_SECONDS,
    poll_seconds: float = POLL_SECONDS, clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[int, Timeline]:
    """For the experiments with no fix to test (KAV-81): create the victim, wait until it is up,
    hold the condition for `hold_seconds`, then clean up. Success here means the experiment ran
    as designed; whether Kaval told a person and proposed nothing is read from the database
    afterwards (`make chaos-check`), because the runner has no database access by design. The
    victim is always deleted on the way out, and so is anything it wrote."""
    timeline = Timeline(experiment, started_at=datetime.now(UTC).isoformat())
    if cluster.victim_exists():
        raise Refused(f"the {experiment} victim already exists; run `make chaos-stop` first")
    t0 = clock()
    cluster.create_victim()
    timeline.mark("victim_created", 0)
    ready_at: float | None = None
    try:
        while True:
            elapsed = clock() - t0
            if not cluster.switch_on():
                timeline.result = "aborted: switch turned off"
                return EXIT_ABORTED, timeline
            if elapsed >= max_seconds:
                timeline.result = f"timeout after {int(max_seconds)} s"
                return EXIT_TIMEOUT, timeline
            if ready_at is None:
                if elapsed >= ready_seconds:
                    timeline.result = f"the victim was not ready within {int(ready_seconds)} s"
                    return EXIT_TIMEOUT, timeline
                if cluster.victim_ready():
                    ready_at = elapsed
                    timeline.mark("victim_ready", elapsed)
            elif elapsed - ready_at >= hold_seconds:
                timeline.mark("held", elapsed)
                timeline.result = (f"held for {int(hold_seconds)} s, then cleaned up: "
                                   "now check what Kaval did (make chaos-check)")
                return EXIT_OK, timeline
            sleep(poll_seconds)
    finally:
        cluster.delete_victim()
        timeline.mark("victim_deleted", clock() - t0)


class KubeCluster:
    """The real thing, behind the `Cluster` protocol so the loop above is testable."""

    victim = VICTIM_NAME

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
            self.apps.read_namespaced_deployment(self.victim, self.namespace)
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
        deployment = self.apps.read_namespaced_deployment(self.victim, self.namespace)
        raw: Any = None
        for c in (deployment.spec.template.spec.containers  # type: ignore[union-attr]
                  if deployment.spec else []):
            raw = ((c.resources.limits if c.resources else None) or {}).get("memory")
            break
        limit_mib = (int(parse_quantity(raw)) // k8s.MIB  # type: ignore[no-untyped-call]
                     if raw else None)
        status = deployment.status
        pods = self.core.list_namespaced_pod(
            self.namespace, label_selector=f"app={self.victim}").items
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
            self.apps.delete_namespaced_deployment(self.victim, self.namespace)
        except ApiException as exc:
            if exc.status != 404:
                raise


class KubeReleaseCluster(KubeCluster):
    """The bad-release experiment's real cluster: the same switch, the same clean-up, a different
    victim."""

    victim = RELEASE_VICTIM_NAME

    def create_victim(self) -> None:
        self.apps.create_namespaced_deployment(
            self.namespace, release_manifest(self.namespace, "v1"))  # type: ignore[arg-type]

    def deliver_bad_release(self) -> None:
        self.apps.patch_namespaced_deployment(
            self.victim, self.namespace, bad_release_patch(self.namespace))

    def release_snapshot(self) -> ReleaseSnapshot:
        deployment = self.apps.read_namespaced_deployment(self.victim, self.namespace)
        template = deployment.spec.template if deployment.spec else None
        labels = (template.metadata.labels if template and template.metadata else None) or {}
        status = deployment.status
        wanted = int(deployment.spec.replicas or 0) if deployment.spec else 0
        generation = int(deployment.metadata.generation or 0) if deployment.metadata else 0
        rolled_out = bool(
            status and wanted >= 1 and int(status.observed_generation or 0) >= generation
            and int(status.updated_replicas or 0) == wanted
            and int(status.replicas or 0) == wanted
            and int(status.ready_replicas or 0) == wanted
            and int(status.unavailable_replicas or 0) == 0)
        crashing = False
        for pod in self.core.list_namespaced_pod(
                self.namespace, label_selector=f"app={self.victim}").items:
            for cs in (pod.status.container_statuses or []) if pod.status else []:
                waiting = cs.state.waiting if cs.state else None
                ended = cs.state.terminated if cs.state else None
                last = cs.last_state.terminated if cs.last_state else None
                if ((waiting is not None and waiting.reason == "CrashLoopBackOff")
                        or (ended is not None and ended.exit_code)
                        or (last is not None and last.exit_code)):
                    crashing = True
        return ReleaseSnapshot(release=labels.get("release"), rolled_out=rolled_out,
                               crashing=crashing)


class _KubeHoldCluster(KubeCluster):
    """Shared by the two escalate-only experiments: a victim that is up when its Deployment is."""

    def victim_ready(self) -> bool:
        status = self.apps.read_namespaced_deployment(self.victim, self.namespace).status
        return bool(status and (status.ready_replicas or 0) >= 1
                    and (status.unavailable_replicas or 0) == 0)


class KubeSlowCluster(_KubeHoldCluster):
    victim = SLOW_VICTIM_NAME

    def create_victim(self) -> None:
        self.core.create_namespaced_service(
            self.namespace, slowpoke_service(self.namespace))  # type: ignore[arg-type]
        self.apps.create_namespaced_deployment(
            self.namespace, slowpoke_manifest(self.namespace))  # type: ignore[arg-type]

    def delete_victim(self) -> None:
        super().delete_victim()
        try:
            self.core.delete_namespaced_service(self.victim, self.namespace)
        except ApiException as exc:
            if exc.status != 404:
                raise


class KubeDiskCluster(_KubeHoldCluster):
    victim = DISK_VICTIM_NAME

    def create_victim(self) -> None:
        self.apps.create_namespaced_deployment(
            self.namespace, diskfill_manifest(self.namespace))  # type: ignore[arg-type]


# name -> (the experiment, the real cluster class it runs against)
EXPERIMENTS: dict[str, Callable[..., tuple[int, Timeline]]] = {
    "oom": run_oom, "badrelease": run_badrelease,
    "slow": lambda cluster: run_hold("slow", cluster),
    "disk": lambda cluster: run_hold("disk", cluster),
}
CLUSTERS: dict[str, type[KubeCluster]] = {
    "oom": KubeCluster, "badrelease": KubeReleaseCluster,
    "slow": KubeSlowCluster, "disk": KubeDiskCluster,
}


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
        cluster = CLUSTERS[args.experiment](namespace, release_namespace, switch_name)
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
