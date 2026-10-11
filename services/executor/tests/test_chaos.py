"""The chaos runner (KAV-79, ADR-0044): refuses when it should, aborts on the switch, always
cleans up. A fake cluster and a fake clock stand in for Kubernetes and for time."""

from __future__ import annotations

import pytest
from kaval_executor import chaos


class FakeCluster:
    """Scripted snapshots, one per poll; the last one repeats."""

    def __init__(self, snaps: list[chaos.Snapshot], *, switch: list[bool] | None = None,
                 exists: bool = False) -> None:
        self.snaps = snaps
        self.switch = switch or [True]
        self.exists = exists
        self.created = 0
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

    def snapshot(self) -> chaos.Snapshot:
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


def _snap(limit: int = 64, ready: bool = False, oom: bool = False) -> chaos.Snapshot:
    return chaos.Snapshot(limit_mib=limit, ready=ready, oom_killed=oom, restarts=0)


def _run(cluster: FakeCluster, **kw: float) -> tuple[int, chaos.Timeline]:
    clock = Clock()
    return chaos.run_oom(cluster, clock=clock, sleep=clock.sleep, poll_seconds=10, **kw)


# ── refusing to start ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("env", ["prod", "production", "", "Staging"])
def test_refuses_outside_local_and_staging(env: str) -> None:
    with pytest.raises(chaos.Refused, match="may not run chaos"):
        chaos.guard(env, switch_on=True)


@pytest.mark.parametrize("env", ["local", "staging"])
def test_allows_local_and_staging_with_the_switch_on(env: str) -> None:
    chaos.guard(env, switch_on=True)


def test_refuses_with_the_switch_off() -> None:
    with pytest.raises(chaos.Refused, match="switch is off"):
        chaos.guard("staging", switch_on=False)


def test_refuses_when_a_victim_is_already_there_and_does_not_delete_it() -> None:
    cluster = FakeCluster([_snap()], exists=True)
    with pytest.raises(chaos.Refused, match="already exists"):
        _run(cluster)
    assert cluster.created == 0
    assert cluster.deleted == 0  # someone else's leftover is not ours to delete


# ── the run ─────────────────────────────────────────────────────────────────────────────

def test_recovers_when_the_limit_is_raised_and_the_program_stays_up() -> None:
    cluster = FakeCluster(
        [_snap(oom=True), _snap(oom=True), _snap(limit=128, ready=True, oom=True)])
    code, timeline = _run(cluster, stable_seconds=30)
    assert code == chaos.EXIT_OK
    assert timeline.result.startswith("recovered")
    assert {"victim_created", "first_oom_killed", "limit_raised", "recovered",
            "victim_deleted"} <= set(timeline.events)
    assert cluster.created == 1
    assert cluster.deleted == 1


def test_a_raised_limit_that_does_not_stay_up_is_not_recovery() -> None:
    flapping = [_snap(limit=128, ready=True), _snap(limit=128, ready=False)] * 20
    code, timeline = _run(FakeCluster(flapping), max_seconds=300, stable_seconds=60)
    assert code == chaos.EXIT_TIMEOUT
    assert "recovered" not in timeline.events


def test_times_out_when_nothing_fixes_it_and_still_cleans_up() -> None:
    cluster = FakeCluster([_snap(oom=True)])
    code, timeline = _run(cluster, max_seconds=100)
    assert code == chaos.EXIT_TIMEOUT
    assert "timeout" in timeline.result
    assert cluster.deleted == 1


def test_turning_the_switch_off_aborts_and_cleans_up() -> None:
    cluster = FakeCluster([_snap(oom=True)], switch=[True, True, False])
    code, timeline = _run(cluster)
    assert code == chaos.EXIT_ABORTED
    assert "switch" in timeline.result
    assert cluster.deleted == 1


def test_an_error_mid_run_still_deletes_the_victim() -> None:
    class Boom(FakeCluster):
        def snapshot(self) -> chaos.Snapshot:
            raise RuntimeError("api down")

    cluster = Boom([_snap()])
    with pytest.raises(RuntimeError):
        _run(cluster)
    assert cluster.deleted == 1


# ── the victim ──────────────────────────────────────────────────────────────────────────

def test_the_victim_really_exceeds_its_limit_and_is_labelled() -> None:
    m = chaos.victim_manifest("kaval-demo-staging")
    container = m["spec"]["template"]["spec"]["containers"][0]
    assert container["resources"]["limits"]["memory"] == "64Mi"
    assert chaos.VICTIM_NEEDS_MIB > 64
    assert f"{chaos.VICTIM_NEEDS_MIB} * 1024 * 1024" in container["command"][2]
    assert m["metadata"]["labels"][chaos.VICTIM_LABEL] == "true"
    assert m["metadata"]["namespace"] == "kaval-demo-staging"


def test_the_victim_would_fit_after_one_doubling() -> None:
    # The fix doubles 64 -> 128. The victim plus Python itself must stay under the 90 % warning
    # line of that, or the experiment would re-alarm after a correct fix.
    assert chaos.VICTIM_NEEDS_MIB + 14 < 0.9 * 128


def test_the_victim_image_is_pinned_by_digest() -> None:
    assert "@sha256:" in chaos.VICTIM_IMAGE
    container = chaos.victim_manifest("x")["spec"]["template"]["spec"]["containers"][0]
    assert container["securityContext"]["runAsNonRoot"] is True


def test_main_refuses_without_its_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("CHAOS_TARGET_NAMESPACE", "POD_NAMESPACE", "CHAOS_SWITCH", "CHAOS_ENVIRONMENT"):
        monkeypatch.delenv(var, raising=False)
    assert chaos.main(["oom"]) == chaos.EXIT_REFUSED
