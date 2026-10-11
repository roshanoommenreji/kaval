"""`rollback_deployment` (KAV-80, ADR-0045): puts a Deployment back to its previous version, and
refuses rather than guesses. The Kubernetes client is replaced by small fakes built from the
client's own model classes, so the shapes are the real ones and no cluster is needed."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from kaval_executor import k8s
from kubernetes import client
from kubernetes.client.exceptions import ApiException

REV = k8s.REVISION_ANNOTATION


def _template(image: str, release: str, hash_label: str | None = None) -> client.V1PodTemplateSpec:
    labels = {"app": "web", "release": release}
    if hash_label:
        labels[k8s.HASH_LABEL] = hash_label
    return client.V1PodTemplateSpec(
        metadata=client.V1ObjectMeta(labels=labels),
        spec=client.V1PodSpec(containers=[client.V1Container(name="app", image=image)]),
    )


def _deployment(revision: int, image: str, release: str) -> client.V1Deployment:
    return client.V1Deployment(
        metadata=client.V1ObjectMeta(name="web", namespace="demo",
                                     annotations={REV: str(revision)}),
        spec=client.V1DeploymentSpec(
            selector=client.V1LabelSelector(match_labels={"app": "web"}),
            template=_template(image, release),
        ),
    )


def _replica_set(revision: int, image: str, release: str,
                 owner: str = "web") -> client.V1ReplicaSet:
    return client.V1ReplicaSet(
        metadata=client.V1ObjectMeta(
            name=f"web-{revision}", annotations={REV: str(revision)},
            owner_references=[client.V1OwnerReference(
                api_version="apps/v1", kind="Deployment", name=owner, uid="u")],
        ),
        spec=client.V1ReplicaSetSpec(
            selector=client.V1LabelSelector(), template=_template(image, release, f"h{revision}")),
    )


class FakeApps:
    def __init__(self, deployment: client.V1Deployment | None,
                 replica_sets: list[client.V1ReplicaSet], rs_owner: str | None = None):
        self.deployment = deployment
        self.replica_sets = replica_sets
        self.rs_owner = rs_owner
        self.patches: list[tuple[str, str, Any]] = []
        self.selectors: list[str] = []

    def read_namespaced_deployment(self, name: str, namespace: str) -> client.V1Deployment:
        if self.deployment is None or name != "web":
            raise ApiException(status=404)
        return self.deployment

    def list_namespaced_replica_set(self, namespace: str, label_selector: str = "") -> Any:
        self.selectors.append(label_selector)
        return client.V1ReplicaSetList(items=self.replica_sets)

    def read_namespaced_replica_set(self, name: str, namespace: str) -> client.V1ReplicaSet:
        refs = ([client.V1OwnerReference(api_version="apps/v1", kind="Deployment",
                                         name=self.rs_owner, uid="u")] if self.rs_owner else None)
        return client.V1ReplicaSet(metadata=client.V1ObjectMeta(name=name, owner_references=refs),
                                   spec=client.V1ReplicaSetSpec(selector=client.V1LabelSelector()))

    def patch_namespaced_deployment(self, name: str, namespace: str, body: Any) -> None:
        self.patches.append((name, namespace, body))


class FakeCore:
    def __init__(self, owner_kind: str | None = "ReplicaSet"):
        self.owner_kind = owner_kind

    def read_namespaced_pod(self, name: str, namespace: str) -> client.V1Pod:
        refs = ([client.V1OwnerReference(api_version="apps/v1", kind=self.owner_kind,
                                         name="web-2", uid="u")] if self.owner_kind else None)
        return client.V1Pod(metadata=client.V1ObjectMeta(name=name, owner_references=refs))


def _install(monkeypatch: pytest.MonkeyPatch, apps: FakeApps, core: FakeCore | None = None) -> None:
    monkeypatch.setattr(client, "AppsV1Api", lambda: apps)
    monkeypatch.setattr(client, "CoreV1Api", lambda: core or FakeCore())


def _bad_release_cluster() -> FakeApps:
    """Revision 1 (good), 2 (good), 3 (bad, current)."""
    return FakeApps(
        _deployment(3, "img:v3-bad", "v3"),
        [_replica_set(1, "img:v1", "v1"), _replica_set(2, "img:v2", "v2"),
         _replica_set(3, "img:v3-bad", "v3")],
    )


def test_goes_back_to_the_version_just_before_the_current_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apps = _bad_release_cluster()
    _install(monkeypatch, apps)
    before, after, out = k8s.rollback_deployment("demo/web")
    assert before == {"deployment": "demo/web", "revision": 3, "images": ["img:v3-bad"]}
    assert after["went_back_to_revision"] == 2
    assert after["images"] == ["img:v2"]
    assert "revision 3 to revision 2" in out
    assert apps.selectors == ["app=web"]


def test_the_patch_replaces_the_whole_template_and_drops_the_hash_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apps = _bad_release_cluster()
    _install(monkeypatch, apps)
    k8s.rollback_deployment("demo/web")
    (name, namespace, body), = apps.patches
    assert (name, namespace) == ("web", "demo")
    # A JSON patch (a list) that REPLACES the template: not a merge that would leave whatever the
    # bad release added behind. The second operation is the rolled-back mark.
    assert isinstance(body, list) and len(body) == 2
    assert body[0]["op"] == "replace" and body[0]["path"] == "/spec/template"
    value = body[0]["value"]
    assert value["spec"]["containers"][0]["image"] == "img:v2"
    assert value["metadata"]["labels"] == {"app": "web", "release": "v2"}  # no pod-template-hash


def test_a_replica_set_of_another_deployment_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    apps = FakeApps(
        _deployment(2, "img:v2-bad", "v2"),
        [_replica_set(1, "img:other", "other", owner="someone-else"),
         _replica_set(2, "img:v2-bad", "v2")],
    )
    _install(monkeypatch, apps)
    with pytest.raises(k8s.CannotRollBack, match="no earlier version"):
        k8s.rollback_deployment("demo/web")
    assert apps.patches == []


def test_refuses_when_there_is_only_one_version(monkeypatch: pytest.MonkeyPatch) -> None:
    apps = FakeApps(_deployment(1, "img:v1", "v1"), [_replica_set(1, "img:v1", "v1")])
    _install(monkeypatch, apps)
    with pytest.raises(k8s.CannotRollBack, match="no earlier version"):
        k8s.rollback_deployment("demo/web")
    assert apps.patches == []


def test_refuses_when_the_earlier_version_is_identical(monkeypatch: pytest.MonkeyPatch) -> None:
    # e.g. a restart bumped the revision without changing the template
    apps = FakeApps(
        _deployment(2, "img:v1", "v1"),
        [_replica_set(1, "img:v1", "v1"), _replica_set(2, "img:v1", "v1")],
    )
    _install(monkeypatch, apps)
    with pytest.raises(k8s.CannotRollBack, match="identical"):
        k8s.rollback_deployment("demo/web")
    assert apps.patches == []


def test_a_pod_target_is_resolved_to_its_deployment(monkeypatch: pytest.MonkeyPatch) -> None:
    apps = _bad_release_cluster()
    apps.rs_owner = "web"
    _install(monkeypatch, apps)
    before, _, _ = k8s.rollback_deployment("demo/web-3-x2klp")
    assert before["deployment"] == "demo/web"
    assert apps.patches[0][0] == "web"


def test_a_bare_pod_is_refused_with_the_rollback_error(monkeypatch: pytest.MonkeyPatch) -> None:
    apps = _bad_release_cluster()
    _install(monkeypatch, apps, FakeCore(owner_kind=None))
    with pytest.raises(k8s.CannotRollBack, match="not owned by a ReplicaSet"):
        k8s.rollback_deployment("demo/some-bare-pod")
    assert apps.patches == []


def test_a_malformed_target_is_refused() -> None:
    with pytest.raises(k8s.UnknownTarget):
        k8s.rollback_deployment("no-slash")


def test_the_refusals_share_a_base_class_with_the_memory_action() -> None:
    assert issubclass(k8s.CannotRollBack, k8s.CannotAct)
    assert issubclass(k8s.CannotRaiseLimit, k8s.CannotAct)


def test_the_action_is_registered_for_the_executor() -> None:
    assert k8s.SUPPORTED_ACTIONS["rollback_deployment"] is k8s.rollback_deployment


# ── a second rollback straight after would go forward to the broken release ───────────────

def test_a_rollback_leaves_a_mark_on_the_deployment(monkeypatch: pytest.MonkeyPatch) -> None:
    apps = _bad_release_cluster()
    _install(monkeypatch, apps)
    k8s.rollback_deployment("demo/web")
    mark = apps.patches[0][2][1]
    assert mark["op"] == "add"
    assert mark["path"] == "/metadata/annotations/kaval.dev~1rolled-back-at"
    datetime.fromisoformat(mark["value"])  # a readable time


def test_refuses_a_second_rollback_soon_after_the_first(monkeypatch: pytest.MonkeyPatch) -> None:
    apps = _bad_release_cluster()
    assert apps.deployment is not None and apps.deployment.metadata is not None
    just_now = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    apps.deployment.metadata.annotations[k8s.ROLLBACK_MARK] = just_now  # type: ignore[index]
    _install(monkeypatch, apps)
    with pytest.raises(k8s.CannotRollBack, match="rolled back .* ago"):
        k8s.rollback_deployment("demo/web")
    assert apps.patches == []


def test_an_old_mark_does_not_block(monkeypatch: pytest.MonkeyPatch) -> None:
    apps = _bad_release_cluster()
    assert apps.deployment is not None and apps.deployment.metadata is not None
    long_ago = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    apps.deployment.metadata.annotations[k8s.ROLLBACK_MARK] = long_ago  # type: ignore[index]
    _install(monkeypatch, apps)
    k8s.rollback_deployment("demo/web")
    assert len(apps.patches) == 1


def test_an_unreadable_mark_does_not_block(monkeypatch: pytest.MonkeyPatch) -> None:
    apps = _bad_release_cluster()
    assert apps.deployment is not None and apps.deployment.metadata is not None
    apps.deployment.metadata.annotations[k8s.ROLLBACK_MARK] = "garbage"  # type: ignore[index]
    _install(monkeypatch, apps)
    k8s.rollback_deployment("demo/web")
    assert len(apps.patches) == 1
