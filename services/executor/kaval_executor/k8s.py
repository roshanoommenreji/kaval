"""The Kubernetes calls the executor is actually allowed to make (KAV-47).

One function per supported `action.type` — not a general-purpose client wrapper, because a
general wrapper invites an action type nobody has reviewed into `SUPPORTED_ACTIONS` by
accident. Today: `restart_pod` only, the flagship `auto`-eligible example `policy/README.md`
already names. The executor's own RBAC (`deploy/charts/kaval/templates/executor.yaml`) is the
real enforcement — `get`/`delete` on pods, nothing else — so a bug here that tried to do more
would fail at the API server, not just at review time.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException


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


# The one place `kaval_executor.executor` looks up a handler by `action.type`. An action
# type with no entry here is refused and recorded as skipped — see `executor.execute_one`.
SUPPORTED_ACTIONS = {"restart_pod": restart_pod}
