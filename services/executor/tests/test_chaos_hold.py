"""The two escalate-only experiments (KAV-81, ADR-0047): a slow web program and a bounded disk fill.
The runner only holds the condition and cleans up; whether Kaval told a person and proposed nothing
is read from the database afterwards. Fake cluster and fake clock, as in test_chaos.py."""

from __future__ import annotations

import pytest
from kaval_executor import chaos


class FakeHoldCluster:
    def __init__(self, *, ready_after: int = 0, switch: list[bool] | None = None,
                 exists: bool = False) -> None:
        self.ready_after = ready_after
        self.switch = switch or [True]
        self.exists = exists
        self.created = 0
        self.deleted = 0
        self._ready_polls = 0
        self._switch_reads = 0

    def switch_on(self) -> bool:
        i = min(self._switch_reads, len(self.switch) - 1)
        self._switch_reads += 1
        return self.switch[i]

    def victim_exists(self) -> bool:
        return self.exists

    def create_victim(self) -> None:
        self.created += 1

    def victim_ready(self) -> bool:
        self._ready_polls += 1
        return self._ready_polls > self.ready_after

    def delete_victim(self) -> None:
        self.deleted += 1


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _run(cluster: FakeHoldCluster, **kw: float) -> tuple[int, chaos.Timeline]:
    clock = Clock()
    return chaos.run_hold("slow", cluster, clock=clock, sleep=clock.sleep, poll_seconds=10, **kw)


def test_it_holds_the_condition_then_cleans_up_and_says_to_check_kaval() -> None:
    cluster = FakeHoldCluster(ready_after=3)
    code, timeline = _run(cluster, hold_seconds=120)
    assert code == chaos.EXIT_OK
    assert cluster.created == 1 and cluster.deleted == 1
    assert timeline.events["victim_ready"] == 30
    assert timeline.events["held"] - timeline.events["victim_ready"] == 120
    assert "make chaos-check" in timeline.result


def test_a_victim_that_never_comes_up_times_out_and_is_still_cleaned_up() -> None:
    cluster = FakeHoldCluster(ready_after=10_000)
    code, timeline = _run(cluster, ready_seconds=60)
    assert code == chaos.EXIT_TIMEOUT and "not ready" in timeline.result
    assert cluster.deleted == 1


def test_turning_the_switch_off_stops_it_at_once_and_cleans_up() -> None:
    cluster = FakeHoldCluster(switch=[True, True, False])
    code, timeline = _run(cluster)
    assert code == chaos.EXIT_ABORTED and "switch" in timeline.result
    assert cluster.deleted == 1


def test_it_will_not_start_on_top_of_a_leftover_victim() -> None:
    cluster = FakeHoldCluster(exists=True)
    with pytest.raises(chaos.Refused):
        _run(cluster)
    assert cluster.created == 0 and cluster.deleted == 0


def test_the_whole_run_is_capped_whatever_the_hold_asks_for() -> None:
    cluster = FakeHoldCluster()
    code, timeline = _run(cluster, hold_seconds=10_000, max_seconds=100)
    assert code == chaos.EXIT_TIMEOUT and "timeout" in timeline.result
    assert cluster.deleted == 1


def test_both_experiments_are_registered_with_their_own_victims() -> None:
    assert {"slow", "disk"} <= set(chaos.EXPERIMENTS) == set(chaos.CLUSTERS)
    assert chaos.CLUSTERS["slow"].victim == chaos.SLOW_VICTIM_NAME
    assert chaos.CLUSTERS["disk"].victim == chaos.DISK_VICTIM_NAME


# ── the victims themselves ─────────────────────────────────────────────────────────────────

def _container(manifest: dict[str, object]) -> dict[str, object]:
    spec = manifest["spec"]["template"]["spec"]  # type: ignore[index]
    return spec["containers"][0]  # type: ignore[no-any-return]


def test_the_slow_program_is_a_compiling_python_program_that_is_slow_after_a_delay() -> None:
    manifest = chaos.slowpoke_manifest("demo")
    program = _container(manifest)["command"][2]  # type: ignore[index]
    compile(program, "slowpoke", "exec")
    assert f"> {chaos.SLOW_FAST_FOR_SECONDS}" in program
    assert f"sleep({chaos.SLOW_BY_SECONDS})" in program
    # Ready by a plain connection test: a request-based check would take it out of service once
    # slow, and the probe would then see "nothing there" (down) instead of "slow".
    assert "tcpSocket" in _container(manifest)["readinessProbe"]  # type: ignore[operator]
    assert manifest["metadata"]["namespace"] == "demo"


def test_the_slow_answer_is_slower_than_the_probes_limit_for_slow() -> None:
    from kaval_collector import probe
    assert chaos.SLOW_BY_SECONDS > probe.SLOW_SECONDS
    assert chaos.SLOW_BY_SECONDS < probe.TIMEOUT  # the plain "slow" case, not a timeout


def test_the_service_selects_only_the_slow_program() -> None:
    svc = chaos.slowpoke_service("demo")
    assert svc["spec"]["selector"] == {"app": chaos.SLOW_VICTIM_NAME}
    assert svc["metadata"]["name"] == chaos.SLOW_VICTIM_NAME


def test_the_disk_fill_is_bounded_twice_and_never_touches_the_node_disk_by_path() -> None:
    manifest = chaos.diskfill_manifest("demo")
    program = _container(manifest)["command"][2]  # type: ignore[index]
    compile(program, "diskfill", "exec")
    assert f"range({chaos.DISK_FILL_MIB})" in program
    assert "'/fill/" in program and "/var" not in program and "hostPath" not in str(manifest)
    spec = manifest["spec"]["template"]["spec"]
    # Capped by the volume AND by the container; and the fill is well inside both.
    assert spec["volumes"][0]["emptyDir"]["sizeLimit"] == chaos.DISK_LIMIT
    assert _container(manifest)["resources"]["limits"]["ephemeral-storage"] == chaos.DISK_LIMIT  # type: ignore[index]
    assert chaos.DISK_FILL_MIB < 4 * 1024
    assert _container(manifest)["securityContext"]["runAsNonRoot"] is True  # type: ignore[index]
