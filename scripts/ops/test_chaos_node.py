"""The lose-the-app-server game day (KAV-80, ADR-0046): refuses what it should, times what it
should. A fake AWS and a fake clock stand in for the cloud and for time."""

from __future__ import annotations

import chaos_node as cn
import pytest

OLD = cn.Node("i-old", "running", "kaval-staging", "staging")
NEW = cn.Node("i-new", "running", "kaval-staging", "staging")
NEWEST = "11111111-2222-3333-4444-55555555555a"

HEALTHY = (f"node_ready=1\npods_ready=7\npods_not_ready=0\ngateway=200\nincidents=4\n"
           f"incident_newest={NEWEST}\n")


class FakeAws:
    """The machine list changes with each poll; `probes` is what the node says, in order."""

    def __init__(self, lists: list[list[cn.Node]], probes: list[str], online: bool = True) -> None:
        self.lists = lists
        self.probes = probes
        self.online = online
        self.ended: list[str] = []
        self.scripts: list[str] = []
        self._n = 0
        self._p = 0

    def nodes(self) -> list[cn.Node]:
        out = self.lists[min(self._n, len(self.lists) - 1)]
        self._n += 1
        return out

    def end(self, instance_id: str) -> None:
        self.ended.append(instance_id)
        self._n = max(self._n, 1)  # from now on the machine list moves on

    def ssm_online(self, instance_id: str) -> bool:
        return self.online

    def on_node(self, instance_id: str, script: str) -> str:
        self.scripts.append(script)
        out = self.probes[min(self._p, len(self.probes) - 1)]
        self._p += 1
        return out


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _run(aws: FakeAws, *, yes: bool = True, **kw: float) -> tuple[int, cn.Timeline]:
    clock = Clock()
    return cn.run(aws, confirm=lambda _n: yes, clock=clock, sleep=clock.sleep,
                  poll_seconds=15, **kw)


def _lists() -> list[list[cn.Node]]:
    return [
        [OLD],                                            # before: the one machine
        [cn.Node("i-old", "shutting-down", "kaval-staging", "staging")],
        [cn.Node("i-old", "terminated", "kaval-staging", "staging"),
         cn.Node("i-new", "pending", "kaval-staging", "staging")],
        [cn.Node("i-old", "terminated", "kaval-staging", "staging"), NEW],
    ]


# ── refusing ────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("node", [
    cn.Node("i-1", "running", "kaval-prod", "prod"),          # prod
    cn.Node("i-2", "running", "kaval-prod", "staging"),       # right tag, wrong group
    cn.Node("i-3", "running", "kaval-staging", "prod"),       # right group, wrong tag
    cn.Node("i-4", "running", "", ""),                        # not in a group at all
])
def test_refuses_anything_that_is_not_the_staging_machine(node: cn.Node) -> None:
    with pytest.raises(cn.Refused, match="only group"):
        cn.guard(node)


def test_refuses_when_staging_is_not_running() -> None:
    with pytest.raises(cn.Refused, match="not running"):
        cn.guard(cn.the_running_node([cn.Node("i-x", "terminated", "kaval-staging", "staging")]))


def test_refuses_to_guess_between_two_running_machines() -> None:
    with pytest.raises(cn.Refused, match="expected exactly one"):
        cn.the_running_node([OLD, NEW])


def test_a_prod_machine_is_never_ended() -> None:
    aws = FakeAws([[cn.Node("i-prod", "running", "kaval-prod", "prod")]], [HEALTHY])
    with pytest.raises(cn.Refused):
        _run(aws)
    assert aws.ended == []


def test_refuses_to_start_when_kaval_is_already_unhealthy() -> None:
    aws = FakeAws([[OLD]], ["node_ready=1\npods_ready=3\npods_not_ready=2\ngateway=503\n"])
    with pytest.raises(cn.Refused, match="not healthy to begin with"):
        _run(aws)
    assert aws.ended == []


def test_nothing_is_ended_without_the_confirmation() -> None:
    aws = FakeAws([[OLD]], [HEALTHY])
    with pytest.raises(cn.Refused, match="not confirmed"):
        _run(aws, yes=False)
    assert aws.ended == []


# ── the run ─────────────────────────────────────────────────────────────────────────────

def test_recovers_and_times_each_step() -> None:
    aws = FakeAws(_lists(), [
        HEALTHY,                                              # before
        "",                                                   # new machine: not answering yet
        "node_ready=1\npods_ready=2\npods_not_ready=5\ngateway=000\nincidents=-1\n",
        HEALTHY + "newest_found=200\n",                       # all back
    ])
    code, timeline = _run(aws)
    assert code == cn.EXIT_OK
    assert aws.ended == ["i-old"]
    assert {"machine_ended", "old_machine_gone", "new_machine_running",
            "session_manager_reachable", "cluster_node_ready", "all_pods_ready",
            "gateway_healthy", "data_intact"} <= set(timeline.events)
    assert timeline.new_instance == "i-new" and timeline.result.startswith("recovered")
    ev = timeline.events
    assert ev["machine_ended"] <= ev["old_machine_gone"] <= ev["new_machine_running"]
    assert ev["new_machine_running"] <= ev["all_pods_ready"]


def test_a_node_that_is_up_but_missing_pods_is_not_recovery() -> None:
    # Only 3 of the 7 pods that were ready before are back, and none are failing: a half-built
    # cluster must not count.
    partial = "node_ready=1\npods_ready=3\npods_not_ready=0\ngateway=200\nincidents=4\n"
    aws = FakeAws(_lists(), [HEALTHY, partial])
    code, timeline = _run(aws, max_seconds=300)
    assert code == cn.EXIT_TIMEOUT
    assert "all_pods_ready" not in timeline.events


def test_lost_data_is_not_recovery() -> None:
    lost = ("node_ready=1\npods_ready=7\npods_not_ready=0\ngateway=200\nincidents=0\n"
            "newest_found=404\n")
    aws = FakeAws(_lists(), [HEALTHY, lost])
    code, timeline = _run(aws, max_seconds=300)
    assert code == cn.EXIT_TIMEOUT
    assert "data_intact" not in timeline.events


def test_times_out_when_no_replacement_ever_comes() -> None:
    aws = FakeAws([[OLD], [cn.Node("i-old", "terminated", "kaval-staging", "staging")]], [HEALTHY])
    code, timeline = _run(aws, max_seconds=120)
    assert code == cn.EXIT_TIMEOUT
    assert "old_machine_gone" in timeline.events and "new_machine_running" not in timeline.events


def test_the_machine_is_not_asked_anything_until_session_manager_is_up() -> None:
    aws = FakeAws(_lists(), [HEALTHY], online=False)
    _run(aws, max_seconds=120)
    assert len(aws.scripts) == 1  # only the "before" probe


def test_the_probe_checks_the_incident_seen_before_the_loss() -> None:
    aws = FakeAws(_lists(), [HEALTHY, HEALTHY + "newest_found=200\n"])
    _run(aws)
    assert f"/v1/incidents/{NEWEST}" in aws.scripts[-1]
    assert f"/v1/incidents/{NEWEST}" not in aws.scripts[0]


def test_an_odd_incident_id_is_never_put_into_a_script() -> None:
    aws = FakeAws([[OLD]], ["x"])
    script = cn.PROBE.replace("NEWEST_CHECK", cn.NEWEST_CHECK.replace("{id}", "x; rm -rf /"))
    assert "rm -rf" in script  # the template itself would allow it...
    assert cn.UUID.match("x; rm -rf /") is None  # ...which is why probe() checks the id first
    cn.probe(aws, "i-old", "x; rm -rf /")
    assert "rm -rf" not in aws.scripts[0]


def test_parse_reads_only_plain_key_value_lines() -> None:
    out = "noise\nnode_ready=1\ngateway=200\nbad line = 3\nincident_newest=none\n"
    assert cn.parse(out) == {"node_ready": "1", "gateway": "200", "incident_newest": "none"}
