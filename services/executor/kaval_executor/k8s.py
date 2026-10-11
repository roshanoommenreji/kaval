"""The Kubernetes calls the executor is actually allowed to make (KAV-47).

One function per supported `action.type` — not a general-purpose client wrapper, because a
general wrapper invites an action type nobody has reviewed into `SUPPORTED_ACTIONS` by
accident. Today: `restart_pod`, the flagship `auto`-eligible example `policy/README.md`
already names, and `increase_memory_limit` (KAV-79). The executor's own RBAC
(`deploy/charts/kaval/templates/executor.yaml`) is the real enforcement — `get`/`delete` on
pods, `get`/`patch` on deployments, `get` on replicasets, in the demo namespace only — so a bug
here that tried to do more would fail at the API server, not just at review time.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from kubernetes.utils import parse_quantity


def load_config() -> None:
    """In-cluster first: this is how the executor actually runs, as the `executor`
    ServiceAccount with its token mounted by the Deployment. Falls back to the local
    kubeconfig so `python -m kaval_executor.executor --once` also works from a laptop
    against a reachable cluster — the same laptop-run affordance `kaval_agent`'s own tools
    already have (Lab 10, Lab 11), not a new exception to the privilege-separation rule:
    whichever identity is active, RBAC still decides what it can do."""
    try:
        config.load_incluster_config()  # type: ignore[no-untyped-call]
    except config.ConfigException:
        config.load_kube_config()  # type: ignore[no-untyped-call]


class UnknownTarget(RuntimeError):
    """`action.target` wasn't `namespace/name`."""


def _split_target(target: str) -> tuple[str, str]:
    if target.count("/") != 1:
        raise UnknownTarget(f"expected 'namespace/name', got {target!r}")
    namespace, name = target.split("/", 1)
    if not namespace or not name:
        raise UnknownTarget(f"expected 'namespace/name', got {target!r}")
    return namespace, name


def restart_pod(target: str) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Delete one named pod. Called "restart" because that is the operator's intent and
    `policy/README.md`'s vocabulary for it — Kubernetes itself has no restart verb. Deleting
    a pod its controller owns is how a restart actually happens: the controller, not this
    function, creates the replacement. A bare pod with no controller just stays deleted,
    which `before_state` records rather than silently assuming a replacement is coming.

    Returns `(before_state, after_state, stdout)` — the three fields
    `kaval_executor.executor` writes straight into one `execution` row.
    """
    namespace, name = _split_target(target)
    v1 = client.CoreV1Api()
    try:
        pod = v1.read_namespaced_pod(name, namespace)
    except ApiException as exc:
        if exc.status == 404:
            return (
                {"found": False},
                {"deleted": False},
                f"pod {target} not found; nothing to restart",
            )
        raise
    # The client library types every field as optional. A pod the API server returns always has
    # all three; the fallbacks only satisfy the type checker and change nothing at runtime.
    metadata = pod.metadata or client.V1ObjectMeta()
    status = pod.status or client.V1PodStatus()
    spec = pod.spec or client.V1PodSpec(containers=[])
    owner = metadata.owner_references[0].kind if metadata.owner_references else None
    before_state = {
        "found": True,
        "phase": status.phase,
        "node": spec.node_name,
        "restart_count": sum(int(c.restart_count or 0) for c in status.container_statuses or []),
        "owner_kind": owner,
        "uid": metadata.uid,
    }
    v1.delete_namespaced_pod(name, namespace)
    after_state = {
        "deleted": True,
        "deleted_at": datetime.now(UTC).isoformat(),
        "replacement": "expected from its controller" if owner
                       else "none — this pod has no owning controller",
    }
    stdout = (
        f"deleted pod {target} (uid {metadata.uid}, was {status.phase}, "
        f"owner={owner or 'none'})"
    )
    return before_state, after_state, stdout


MIB = 1024 * 1024
# The most memory this action will ever give one container (ADR-0044). A program that needs more
# than this is a leak or a sizing question for a person, not something to keep doubling.
MEMORY_LIMIT_CEILING_MIB = 512


class CannotRaiseLimit(RuntimeError):
    """The memory limit cannot be raised safely; a person has to look. Becomes a failed,
    recorded execution carrying this message."""


def next_memory_limit_mib(current_bytes: int) -> int:
    """Double, rounded up to whole MiB, never above the ceiling. Raises `CannotRaiseLimit` when
    the limit is already at (or past) the ceiling: nothing left to give."""
    current_mib = -(-current_bytes // MIB)
    if current_mib >= MEMORY_LIMIT_CEILING_MIB:
        raise CannotRaiseLimit(
            f"memory limit is already {current_mib} MiB, at or above the "
            f"{MEMORY_LIMIT_CEILING_MIB} MiB ceiling; a person has to decide"
        )
    return min(current_mib * 2, MEMORY_LIMIT_CEILING_MIB)


def _deployment_of_pod(apps: client.AppsV1Api, v1: client.CoreV1Api, namespace: str,
                       pod_name: str) -> str:
    """The deployment that owns a pod, through its ReplicaSet. The incident names a pod
    (`ns/pod-7f9c-x2klp`), the fix belongs on the deployment, so the model may name either."""
    pod = v1.read_namespaced_pod(pod_name, namespace)
    refs = (pod.metadata.owner_references if pod.metadata else None) or []
    rs_name = next((r.name for r in refs if r.kind == "ReplicaSet"), None)
    if rs_name is None:
        raise CannotRaiseLimit(f"pod {namespace}/{pod_name} is not owned by a ReplicaSet")
    rs = apps.read_namespaced_replica_set(rs_name, namespace)
    rs_refs = (rs.metadata.owner_references if rs.metadata else None) or []
    deployment = next((r.name for r in rs_refs if r.kind == "Deployment"), None)
    if deployment is None:
        raise CannotRaiseLimit(f"pod {namespace}/{pod_name} does not belong to a Deployment")
    return deployment


def increase_memory_limit(target: str) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Double one container's memory limit, up to a fixed ceiling (KAV-79, ADR-0044).

    `target` is a Deployment (`namespace/name`) or one of its pods. The patch goes on the
    Deployment, so the change survives the next restart and rolls the pods; a patch on a pod
    would be lost the moment it died. Only the memory limit changes; requests, the image and
    everything else are left alone. Refuses, rather than guesses, when the Deployment has no
    memory limit, has several containers with one (which container?), or is already at the
    ceiling. Reversible: the before-state in the execution row says what to set it back to.
    """
    namespace, name = _split_target(target)
    apps = client.AppsV1Api()
    v1 = client.CoreV1Api()
    try:
        deployment = apps.read_namespaced_deployment(name, namespace)
    except ApiException as exc:
        if exc.status != 404:
            raise
        name = _deployment_of_pod(apps, v1, namespace, name)
        deployment = apps.read_namespaced_deployment(name, namespace)

    spec = deployment.spec
    containers = (spec.template.spec.containers if spec and spec.template.spec else None) or []
    limited = [c for c in containers
               if c.resources and c.resources.limits and "memory" in c.resources.limits]
    if not limited:
        raise CannotRaiseLimit(f"deployment {namespace}/{name} has no memory limit to raise")
    if len(limited) > 1:
        raise CannotRaiseLimit(
            f"deployment {namespace}/{name} has {len(limited)} containers with a memory "
            "limit; not guessing which one"
        )
    container = limited[0]
    old = ((container.resources.limits if container.resources else None) or {})["memory"]
    new_mib = next_memory_limit_mib(int(parse_quantity(old)))  # type: ignore[no-untyped-call]
    new = f"{new_mib}Mi"

    patch = {"spec": {"template": {"spec": {"containers": [
        {"name": container.name, "resources": {"limits": {"memory": new}}},
    ]}}}}
    apps.patch_namespaced_deployment(name, namespace, patch)
    before_state = {"deployment": f"{namespace}/{name}", "container": container.name,
                    "memory_limit": old}
    after_state = {"memory_limit": new, "patched_at": datetime.now(UTC).isoformat(),
                   "rollout": "the Deployment replaces its pods with the new limit"}
    stdout = (f"deployment {namespace}/{name}, container {container.name}: "
              f"memory limit {old} -> {new}")
    return before_state, after_state, stdout


# The one place `kaval_executor.executor` looks up a handler by `action.type`. An action
# type with no entry here is refused and recorded as skipped — see `executor.execute_one`.
SUPPORTED_ACTIONS = {
    "restart_pod": restart_pod,
    "increase_memory_limit": increase_memory_limit,
}
