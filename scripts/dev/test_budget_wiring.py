"""The spending brake's settings must say the same thing everywhere (KAV-96).

The ceiling and its three lines live in production's Terraform values; the project rules and the
budget plan repeat them in words. When a number changes in one place and not the others, the next
person trusts the wrong one. These tests read each place and compare, and they also check that the
brake is armed and names every server group that can run while the bill is high."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(*parts: str) -> str:
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def _tfvars() -> dict[str, int]:
    text = _read("infra", "envs", "prod", "terraform.tfvars")
    names = ("monthly_limit_usd", "alert_1_usd", "alert_2_usd", "hard_stop_usd")
    found = {n: re.search(rf"(?m)^{n}\s*=\s*(\d+)\s*$", text) for n in names}
    assert all(found.values()), found
    return {n: int(m.group(1)) for n, m in found.items() if m}


def _module_default(name: str) -> int:
    text = _read("infra", "modules", "budget", "variables.tf")
    found = re.search(rf'variable "{name}".*?default\s*=\s*(\d+)', text, re.S)
    assert found is not None, name
    return int(found.group(1))


def test_the_lines_are_in_order_below_the_ceiling() -> None:
    v = _tfvars()
    assert v["alert_1_usd"] < v["alert_2_usd"] < v["hard_stop_usd"] < v["monthly_limit_usd"]


def test_the_modules_defaults_equal_productions_values() -> None:
    for name, value in _tfvars().items():
        assert _module_default(name) == value, name


def test_the_project_rules_state_the_same_numbers() -> None:
    v = _tfvars()
    rules = _read("CLAUDE.md")
    assert f"${v['monthly_limit_usd']}/month" in rules
    assert (
        f"Alerts at ${v['alert_1_usd']} / ${v['alert_2_usd']}, hard stop at ${v['hard_stop_usd']}"
        in rules
    )


def test_the_budget_plan_states_the_same_numbers() -> None:
    v = _tfvars()
    plan = _read("docs", "cost", "budget-plan.md")
    for name in ("alert_1_usd", "alert_2_usd", "hard_stop_usd"):
        assert f"| MTD ≥ ${v[name]} |" in plan, name
    four = " / ".join(
        f"${v[n]}" for n in ("alert_1_usd", "alert_2_usd", "hard_stop_usd", "monthly_limit_usd")
    )
    assert four in plan


def test_the_brake_is_armed_in_production() -> None:
    main = _read("infra", "envs", "prod", "main.tf")
    block = main.split('module "budget"')[1].split("\n}")[0]
    assert "hard_stop_dry_run = false" in block
    assert "stop_tagged_instances = true" in block


def test_the_brake_names_production_and_staging() -> None:
    block = _read("infra", "envs", "prod", "main.tf").split('module "budget"')[1].split("\n}")[0]
    found = re.search(r"asg_names\s*=\s*\[(.*?)\]", block)
    assert found is not None
    listed = re.findall(r'"([^"]+)"', found.group(1))
    staging = re.search(
        r'(?m)^\s*name_prefix\s*=\s*"([^"]+)"', _read("infra", "envs", "staging", "main.tf")
    )
    assert staging is not None
    # A server inside a group is skipped by the instance action, so each group must be named.
    assert "kaval-prod" in listed and staging.group(1) in listed
