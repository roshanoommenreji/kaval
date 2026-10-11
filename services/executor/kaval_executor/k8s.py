"""The Kubernetes calls the executor is actually allowed to make (KAV-47).

One function per supported `action.type` — not a general-purpose client wrapper, because a
general wrapper invites an action type nobody has reviewed into `SUPPORTED_ACTIONS` by
accident. Today: `restart_pod`, the flagship `auto`-eligible example `policy/README.md`
already names, `increase_memory_limit` (KAV-79) and `rollback_deployment` (KAV-80). The
executor's own RBAC (`deploy/charts/kaval/templates/executor.yaml`) is the real enforcement —
`get`/`delete` on pods, `get`/`patch` on deployments, `get`/`list` on replicasets, in the demo
namespace only — so a bug here that tried to do more would fail at the API server, not just at
review time.
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


class CannotAct(RuntimeError):
    """An action refuses to act because it cannot be done safely; a person has to look. Becomes a
    failed, recorded execution carrying the message."""


class CannotRaiseLimit(CannotAct):
    """The memory limit cannot be raised safely."""


class CannotRollBack(CannotAct):
    """The Deployment cannot be rolled back safely."""


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
                       pod_name: str, refuse: type[CannotAct] = CannotRaiseLimit) -> str:
    """The deployment that owns a pod, through its ReplicaSet. The incident names a pod
    (`ns/pod-7f9c-x2klp`), the fix belongs on the deployment, so the model may name either."""
    pod = v1.read_namespaced_pod(pod_name, namespace)
    refs = (pod.metadata.owner_references if pod.metadata else None) or []
    rs_name = next((r.name for r in refs if r.kind == "ReplicaSet"), None)
    if rs_name is None:
        raise refuse(f"pod {namespace}/{pod_name} is not owned by a ReplicaSet")
    rs = apps.read_namespaced_replica_set(rs_name, namespace)
    rs_refs = (rs.metadata.owner_references if rs.metadata else None) or []
    deployment = next((r.name for r in rs_refs if r.kind == "Deployment"), None)
    if deployment is None:
        raise refuse(f"pod {namespace}/{pod_name} does not belong to a Deployment")
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


REVISION_ANNOTATION = "deployment.kubernetes.io/revision"
HASH_LABEL = "pod-template-hash"
# Left on a Deployment by a rollback. A rollback makes the old version the NEWEST revision, so a
# second rollback straight after would go "back" to the broken release we just left. Within this
# window the action refuses and a person decides.
ROLLBACK_MARK = "kaval.dev/rolled-back-at"
ROLLBACK_COOLDOWN_SECONDS = 3600


def _revision(obj: Any) -> int:
    annotations = (obj.metadata.annotations if obj.metadata else None) or {}
    try:
        return int(annotations.get(REVISION_ANNOTATION, 0))
    except ValueError:
        return 0


def _images(template: Any) -> list[str]:
    containers = (template.spec.containers if template and template.spec else None) or []
    return [str(c.image) for c in containers]


def _refuse_if_just_rolled_back(deployment: str, mark: str) -> None:
    try:
        when = datetime.fromisoformat(mark)
    except ValueError:
        return  # a mark nobody can read does not block; the other checks still apply
    age = (datetime.now(UTC) - when).total_seconds()
    if 0 <= age < ROLLBACK_COOLDOWN_SECONDS:
        raise CannotRollBack(
            f"deployment {deployment} was rolled back {int(age)} s ago; rolling back again would "
            "go forward to the release just left. A person has to look")


def rollback_deployment(target: str) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Put a Deployment back to the version it ran before the current one (KAV-80, ADR-0045).

    The same thing `kubectl rollout undo` does: take the pod template of the previous
    ReplicaSet and make it the Deployment's template again. A restart cannot fix a bad release
    (the pod comes back running the same broken version); this can. `target` is a Deployment
    (`namespace/name`) or one of its pods. Refuses, rather than guesses, when there is no earlier
    version or the earlier version is identical to the current one (nothing to go back to). It
    cannot tell whether the earlier version is any good; the outcome check five minutes later
    does. Reversible: the before-state names the version it left, and rolling forward is the
    same operation by a person.
    """
    namespace, name = _split_target(target)
    apps = client.AppsV1Api()
    v1 = client.CoreV1Api()
    try:
        deployment = apps.read_namespaced_deployment(name, namespace)
    except ApiException as exc:
        if exc.status != 404:
            raise
        name = _deployment_of_pod(apps, v1, namespace, name, CannotRollBack)
        deployment = apps.read_namespaced_deployment(name, namespace)

    annotations = (deployment.metadata.annotations if deployment.metadata else None) or {}
    if ROLLBACK_MARK in annotations:
        _refuse_if_just_rolled_back(f"{namespace}/{name}", annotations[ROLLBACK_MARK])

    spec = deployment.spec
    if spec is None or spec.selector is None:
        raise CannotRollBack(f"deployment {namespace}/{name} has no selector; not guessing")
    selector = ",".join(f"{k}={v}" for k, v in sorted((spec.selector.match_labels or {}).items()))
    owned = [
        rs for rs in apps.list_namespaced_replica_set(namespace, label_selector=selector).items
        if any(r.kind == "Deployment" and r.name == name
               for r in ((rs.metadata.owner_references if rs.metadata else None) or []))
    ]
    current = _revision(deployment)
    earlier = sorted((rs for rs in owned if 0 < _revision(rs) < current), key=_revision)
    if not earlier:
        raise CannotRollBack(f"deployment {namespace}/{name} has no earlier version to go back to")
    previous = earlier[-1]
    if previous.spec is None or previous.spec.template is None:
        raise CannotRollBack(f"the earlier version of {namespace}/{name} has no pod template")

    api = client.ApiClient()
    template: dict[str, Any] = api.sanitize_for_serialization(  # type: ignore[no-untyped-call]
        previous.spec.template)
    # The ReplicaSet's own hash label must not be copied into the Deployment (kubectl strips it).
    ((template.get("metadata") or {}).get("labels") or {}).pop(HASH_LABEL, None)
    current_template: dict[str, Any] = api.sanitize_for_serialization(  # type: ignore[no-untyped-call]
        spec.template)
    ((current_template.get("metadata") or {}).get("labels") or {}).pop(HASH_LABEL, None)
    if template == current_template:
        raise CannotRollBack(
            f"deployment {namespace}/{name}: the earlier version is identical to the current "
            "one; a rollback would change nothing"
        )

    # A JSON patch that REPLACES the template, so anything the bad release added (an env var, a
    # volume) goes too; a merge patch would only overlay the old template onto the new one.
    now = datetime.now(UTC)
    apps.patch_namespaced_deployment(name, namespace, [
        {"op": "replace", "path": "/spec/template", "value": template},
        # "/" inside a JSON-pointer key is written "~1"; "add" also replaces an existing mark.
        {"op": "add", "path": "/metadata/annotations/" + ROLLBACK_MARK.replace("/", "~1"),
         "value": now.isoformat()},
    ])
    before_state = {"deployment": f"{namespace}/{name}", "revision": current,
                    "images": _images(spec.template)}
    after_state = {"went_back_to_revision": _revision(previous),
                   "images": _images(previous.spec.template),
                   "patched_at": now.isoformat(),
                   "rollout": "the Deployment replaces its pods with the earlier version"}
    stdout = (f"deployment {namespace}/{name}: rolled back from revision {current} "
              f"to revision {_revision(previous)}")
    return before_state, after_state, stdout


# The one place `kaval_executor.executor` looks up a handler by `action.type`. An action
# type with no entry here is refused and recorded as skipped — see `executor.execute_one`.
SUPPORTED_ACTIONS = {
    "restart_pod": restart_pod,
    "increase_memory_limit": increase_memory_limit,
    "rollback_deployment": rollback_deployment,
}
