"""`increase_memory_limit` (KAV-79, ADR-0044): doubles a memory limit, never past the ceiling,
and refuses rather than guesses. The Kubernetes client is replaced by small fakes built from the
client's own model classes, so the shapes are the real ones and no cluster is needed."""

from __future__ import annotations

from typing import Any

import pytest
from kaval_executor import k8s
from kubernetes import client
from kubernetes.client.exceptions import ApiException


def _deployment(limits: list[str | None], names: list[str] | None = None) -> client.V1Deployment:
    names = names or [f"c{i}" for i in range(len(limits))]
    containers = [
        client.V1Container(
            name=n, image="x",
            resources=client.V1ResourceRequirements(
                limits={"memory": lim} if lim else None, requests={"memory": "32Mi"},
            ),
        )
        for n, lim in zip(names, limits, strict=True)
    ]
    return client.V1Deployment(
        metadata=client.V1ObjectMeta(name="oomer", namespace="demo"),
        spec=client.V1DeploymentSpec(
            selector=client.V1LabelSelector(),
            template=client.V1PodTemplateSpec(spec=client.V1PodSpec(containers=containers)),
        ),
    )


class FakeApps:
    def __init__(self, deployment: client.V1Deployment | None, rs_owner: str | None = None):
        self.deployment = deployment
        self.patches: list[tuple[str, str, Any]] = []
        self.rs_owner = rs_owner

    def read_namespaced_deployment(self, name: str, namespace: str) -> client.V1Deployment:
        if self.deployment is None or name != "oomer":
            raise ApiException(status=404)
        return self.deployment

    def patch_namespaced_deployment(self, name: str, namespace: str, body: Any) -> None:
        self.patches.append((name, namespace, body))

    def read_namespaced_replica_set(self, name: str, namespace: str) -> client.V1ReplicaSet:
        refs = ([client.V1OwnerReference(api_version="apps/v1", kind="Deployment",
                                         name=self.rs_owner, uid="u")]
                if self.rs_owner else None)
        return client.V1ReplicaSet(metadata=client.V1ObjectMeta(name=name, owner_references=refs),
                                   spec=client.V1ReplicaSetSpec(selector=client.V1LabelSelector()))


class FakeCore:
    def __init__(self, owner_kind: str | None = "ReplicaSet"):
        self.owner_kind = owner_kind

    def read_namespaced_pod(self, name: str, namespace: str) -> client.V1Pod:
        refs = ([client.V1OwnerReference(api_version="apps/v1", kind=self.owner_kind,
                                         name="oomer-7f9c", uid="u")]
                if self.owner_kind else None)
        return client.V1Pod(metadata=client.V1ObjectMeta(name=name, owner_references=refs))


def _install(monkeypatch: pytest.MonkeyPatch, apps: FakeApps, core: FakeCore | None = None) -> None:
    monkeypatch.setattr(client, "AppsV1Api", lambda: apps)
    monkeypatch.setattr(client, "CoreV1Api", lambda: core or FakeCore())


# ── the arithmetic ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("mib", "expected"), [(64, 128), (100, 200), (256, 512), (300, 512)])
def test_next_limit_doubles_and_stops_at_the_ceiling(mib: int, expected: int) -> None:
    assert k8s.next_memory_limit_mib(mib * k8s.MIB) == expected


@pytest.mark.parametrize("mib", [512, 600, 4096])
def test_next_limit_refuses_at_or_above_the_ceiling(mib: int) -> None:
    with pytest.raises(k8s.CannotRaiseLimit, match="ceiling"):
        k8s.next_memory_limit_mib(mib * k8s.MIB)


def test_next_limit_rounds_a_partial_mib_up() -> None:
    assert k8s.next_memory_limit_mib(64 * k8s.MIB + 1) == 130


# ── the action ──────────────────────────────────────────────────────────────────────────

def test_patches_the_deployment_with_only_the_memory_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    apps = FakeApps(_deployment(["64Mi"], ["app"]))
    _install(monkeypatch, apps)
    before, after, out = k8s.increase_memory_limit("demo/oomer")
    assert before == {"deployment": "demo/oomer", "container": "app", "memory_limit": "64Mi"}
    assert after["memory_limit"] == "128Mi"
    assert "64Mi -> 128Mi" in out
    (name, namespace, body), = apps.patches
    assert (name, namespace) == ("oomer", "demo")
    # Exactly one container, exactly one field: no requests, no image, nothing else.
    assert body == {"spec": {"template": {"spec": {"containers": [
        {"name": "app", "resources": {"limits": {"memory": "128Mi"}}}]}}}}


def test_a_pod_target_is_resolved_to_its_deployment(monkeypatch: pytest.MonkeyPatch) -> None:
    apps = FakeApps(_deployment(["64Mi"]), rs_owner="oomer")
    _install(monkeypatch, apps)
    before, _, _ = k8s.increase_memory_limit("demo/oomer-7f9c-x2klp")
    assert before["deployment"] == "demo/oomer"
    assert apps.patches[0][0] == "oomer"


def test_refuses_at_the_ceiling_and_patches_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    apps = FakeApps(_deployment(["512Mi"]))
    _install(monkeypatch, apps)
    with pytest.raises(k8s.CannotRaiseLimit, match="ceiling"):
        k8s.increase_memory_limit("demo/oomer")
    assert apps.patches == []


def test_refuses_when_there_is_no_memory_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    apps = FakeApps(_deployment([None]))
    _install(monkeypatch, apps)
    with pytest.raises(k8s.CannotRaiseLimit, match="no memory limit"):
        k8s.increase_memory_limit("demo/oomer")
    assert apps.patches == []


def test_refuses_to_guess_between_containers(monkeypatch: pytest.MonkeyPatch) -> None:
    apps = FakeApps(_deployment(["64Mi", "128Mi"]))
    _install(monkeypatch, apps)
    with pytest.raises(k8s.CannotRaiseLimit, match="not guessing"):
        k8s.increase_memory_limit("demo/oomer")
    assert apps.patches == []


def test_a_sidecar_without_a_limit_does_not_make_it_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apps = FakeApps(_deployment(["64Mi", None], ["app", "sidecar"]))
    _install(monkeypatch, apps)
    k8s.increase_memory_limit("demo/oomer")
    assert apps.patches[0][2]["spec"]["template"]["spec"]["containers"][0]["name"] == "app"


def test_a_bare_pod_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    apps = FakeApps(_deployment(["64Mi"]))
    _install(monkeypatch, apps, FakeCore(owner_kind=None))
    with pytest.raises(k8s.CannotRaiseLimit, match="not owned by a ReplicaSet"):
        k8s.increase_memory_limit("demo/some-bare-pod")
    assert apps.patches == []


def test_a_malformed_target_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, FakeApps(None))
    with pytest.raises(k8s.UnknownTarget):
        k8s.increase_memory_limit("no-slash")


def test_the_action_is_registered_for_the_executor() -> None:
    assert k8s.SUPPORTED_ACTIONS["increase_memory_limit"] is k8s.increase_memory_limit
