"""The bad-release experiment (KAV-80, ADR-0045): a healthy v1, then a v2 that crashes at start-up,
then watching for Kaval's rollback to hold. Fake cluster and fake clock, as in test_chaos.py."""

from __future__ import annotations

from typing import Any

import pytest
from kaval_executor import chaos


def _snap(release: str = "v1", rolled_out: bool = True, crashing: bool = False,
          ) -> chaos.ReleaseSnapshot:
    return chaos.ReleaseSnapshot(release=release, rolled_out=rolled_out, crashing=crashing)


class FakeReleaseCluster:
    """Scripted snapshots, one per poll; the last one repeats."""

    def __init__(self, snaps: list[chaos.ReleaseSnapshot], *, switch: list[bool] | None = None,
                 exists: bool = False) -> None:
        self.snaps = snaps
        self.switch = switch or [True]
        self.exists = exists
        self.created = 0
        self.delivered = 0
        self.deleted = 0
        self._polls = 0
        self._switch_reads = 0

    def switch_on(self) -> bool:
        i = min(self._switch_reads, len(self.switch) - 1)
        self._switch_reads += 1
        return self.switch[i]

    def victim_exists(self) -> bool:
        return self.exists

    def create_victim(self) -> None:
        self.created += 1

    def deliver_bad_release(self) -> None:
        self.delivered += 1

    def release_snapshot(self) -> chaos.ReleaseSnapshot:
        snap = self.snaps[min(self._polls, len(self.snaps) - 1)]
        self._polls += 1
        return snap

    def delete_victim(self) -> None:
        self.deleted += 1


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _run(cluster: FakeReleaseCluster, **kw: float) -> tuple[int, chaos.Timeline]:
    clock = Clock()
    return chaos.run_badrelease(cluster, clock=clock, sleep=clock.sleep, poll_seconds=10, **kw)


BAD = _snap("v2", rolled_out=False, crashing=True)


def test_recovers_when_the_release_is_rolled_back_and_stays_up() -> None:
    cluster = FakeReleaseCluster([
        _snap("v1", rolled_out=False),  # still starting
        _snap("v1"),                    # healthy -> the bad release is delivered
        BAD, BAD,
        _snap("v1", rolled_out=False),  # rollback begun
        _snap("v1"),                    # and settled
    ])
    code, timeline = _run(cluster, stable_seconds=30)
    assert code == chaos.EXIT_OK
    assert timeline.result.startswith("recovered")
    assert {"victim_created", "v1_healthy", "bad_release_delivered", "first_crash",
            "rolled_back", "recovered", "victim_deleted"} <= set(timeline.events)
    assert cluster.created == 1 and cluster.delivered == 1 and cluster.deleted == 1


def test_the_bad_release_is_not_delivered_until_v1_is_healthy() -> None:
    cluster = FakeReleaseCluster([_snap("v1", rolled_out=False)])
    code, timeline = _run(cluster, healthy_seconds=60)
    assert code == chaos.EXIT_TIMEOUT
    assert "v1 did not come up" in timeline.result
    assert cluster.delivered == 0
    assert cluster.deleted == 1


def test_a_rollback_that_does_not_settle_is_not_recovery() -> None:
    flapping = [_snap("v1"), BAD] + [_snap("v1"), _snap("v1", rolled_out=False)] * 20
    code, timeline = _run(FakeReleaseCluster(flapping), max_seconds=300, stable_seconds=60)
    assert code == chaos.EXIT_TIMEOUT
    assert "recovered" not in timeline.events


def test_a_restart_is_not_a_fix() -> None:
    # Kaval restarts the pod: the pod comes back running the same v2, still crashing.
    code, timeline = _run(FakeReleaseCluster([_snap("v1"), BAD]), max_seconds=200)
    assert code == chaos.EXIT_TIMEOUT
    assert "recovered" not in timeline.events and "rolled_back" not in timeline.events


def test_times_out_when_nothing_fixes_it_and_still_cleans_up() -> None:
    cluster = FakeReleaseCluster([_snap("v1"), BAD])
    code, timeline = _run(cluster, max_seconds=100)
    assert code == chaos.EXIT_TIMEOUT
    assert "timeout" in timeline.result
    assert cluster.deleted == 1


def test_turning_the_switch_off_aborts_and_cleans_up() -> None:
    cluster = FakeReleaseCluster([_snap("v1"), BAD], switch=[True, True, True, False])
    code, timeline = _run(cluster)
    assert code == chaos.EXIT_ABORTED
    assert "switch" in timeline.result
    assert cluster.deleted == 1


def test_refuses_when_a_victim_is_already_there_and_does_not_delete_it() -> None:
    cluster = FakeReleaseCluster([_snap()], exists=True)
    with pytest.raises(chaos.Refused, match="already exists"):
        _run(cluster)
    assert cluster.created == 0 and cluster.deleted == 0


def test_an_error_mid_run_still_deletes_the_victim() -> None:
    class Boom(FakeReleaseCluster):
        def release_snapshot(self) -> chaos.ReleaseSnapshot:
            raise RuntimeError("api down")

    cluster = Boom([_snap()])
    with pytest.raises(RuntimeError):
        _run(cluster)
    assert cluster.deleted == 1


# ── the two versions ────────────────────────────────────────────────────────────────────

def _container(release: str) -> dict[str, Any]:
    manifest: dict[str, Any] = chaos.release_manifest("kaval-demo-staging", release)
    container: dict[str, Any] = manifest["spec"]["template"]["spec"]["containers"][0]
    return container


def test_v1_stays_up_and_v2_exits_at_once() -> None:
    v1, v2 = _container("v1")["command"], _container("v2")["command"]
    assert "sleep" in v1[2] and "exit" not in v1[2]
    assert "sys.exit(1)" in v2[2]
    assert v1 != v2  # two genuinely different revisions in the rollout history


def test_each_version_carries_its_label_and_the_victim_label() -> None:
    for release in ("v1", "v2"):
        m = chaos.release_manifest("kaval-demo-staging", release)
        labels = m["spec"]["template"]["metadata"]["labels"]
        assert labels["release"] == release and labels[chaos.VICTIM_LABEL] == "true"
        assert m["metadata"]["name"] == chaos.RELEASE_VICTIM_NAME
        assert m["metadata"]["namespace"] == "kaval-demo-staging"


def test_the_bad_release_patch_changes_the_label_and_the_command_only() -> None:
    patch = chaos.bad_release_patch("kaval-demo-staging")
    template = patch["spec"]["template"]
    assert template["metadata"]["labels"] == {"release": "v2"}
    container, = template["spec"]["containers"]
    assert set(container) == {"name", "command"}
    assert container["command"] == _container("v2")["command"]


def test_the_victims_are_pinned_and_unprivileged() -> None:
    for release in ("v1", "v2"):
        c = _container(release)
        assert "@sha256:" in str(c["image"])
        assert c["securityContext"]["runAsNonRoot"] is True


def test_the_registry_knows_both_experiments() -> None:
    assert set(chaos.EXPERIMENTS) == {"oom", "badrelease"}
    assert set(chaos.CLUSTERS) == set(chaos.EXPERIMENTS)


def test_main_refuses_badrelease_without_its_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("CHAOS_TARGET_NAMESPACE", "POD_NAMESPACE", "CHAOS_SWITCH", "CHAOS_ENVIRONMENT"):
        monkeypatch.delenv(var, raising=False)
    assert chaos.main(["badrelease"]) == chaos.EXIT_REFUSED
