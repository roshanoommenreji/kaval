"""Which causes are for a person (KAV-81, ADR-0047), and that the places that know it agree:
`kaval_shared.health` (agent, gateway) and `policy/policy.rego` (agent and executor)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from kaval_shared import health

REGO = Path(__file__).resolve().parents[3] / "policy" / "policy.rego"


@pytest.mark.parametrize("cause", [
    "disk_pressure", "latency_high", "kaval_loop_stalled", "kaval_prometheus_unreachable",
])
def test_these_causes_are_for_a_person(cause: str) -> None:
    assert health.is_people_only_cause(cause)
    assert health.is_people_only_fingerprint(f"{cause}:k8s:somewhere/thing")


@pytest.mark.parametrize("cause", [
    "oom_killed", "crashloop", "memory_pressure", "cost_spike", "disk", "latency", "pressure",
    "disk_pressure_ok", "",
])
def test_these_are_not(cause: str) -> None:
    assert not health.is_people_only_cause(cause)


def test_the_policy_file_lists_exactly_the_same_causes() -> None:
    text = REGO.read_text(encoding="utf-8")
    match = re.search(r"people_only_causes\s*:=\s*\{([^}]*)\}", text)
    assert match is not None, "policy.rego no longer defines people_only_causes"
    in_rego = set(re.findall(r'"([a-z_]+)"', match.group(1)))
    assert in_rego == set(health.PEOPLE_ONLY_CAUSES)
    assert f'startswith(input.cause, "{health.PREFIX}")' in text


def test_every_signal_that_names_one_of_these_causes_names_a_listed_one() -> None:
    """Every signal's cause is people-only, except the two that removal (KAV-95) can act on."""
    assert (
        set(health.PEOPLE_ONLY_SIGNALS.values()) - health.REMOVABLE_CAUSES
        == set(health.PEOPLE_ONLY_CAUSES)
    )


@pytest.mark.parametrize("kind", [
    "address_unattached", "server_overstay", "resource_unlabelled", "removal_failed",
])
def test_each_waste_finding_is_for_a_person_by_its_signal_kind(kind: str) -> None:
    """KAV-94: these findings have no automated fix; KAV-95 removes only the two kinds below."""
    assert kind in health.PEOPLE_ONLY_SIGNALS
    assert health.is_people_only_cause(health.PEOPLE_ONLY_SIGNALS[kind])


def test_an_unattached_disk_is_not_people_only() -> None:
    assert "volume_unattached" not in health.PEOPLE_ONLY_SIGNALS


@pytest.mark.parametrize("cause", ["idle_volume", "snapshot_past_retention"])
def test_the_two_removable_causes_are_not_people_only(cause: str) -> None:
    """KAV-95, ADR-0063: removal takes exactly these two out of the people-only list, on purpose."""
    assert cause in health.REMOVABLE_CAUSES and not health.is_people_only_cause(cause)
    assert health.REMOVABLE_CAUSES == {"idle_volume", "snapshot_past_retention"}


def test_a_removal_that_failed_is_for_a_person_never_for_a_fix() -> None:
    assert health.is_people_only_cause("removal_failed")
