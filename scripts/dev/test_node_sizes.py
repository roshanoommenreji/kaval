"""The app server may fall back to a second size when AWS has none of the first (KAV-163, ADR-0103).

On 2026-11-17 (staging) and 2026-11-18 (production) AWS had no t4g.medium in any Mumbai zone and
the server group failed every launch for an hour or more. The node module now takes a list of
sizes and the group tries them in order. Offline rules, no AWS: each rule has a checker that the
tests also run against a broken input, so a rule that stopped working would fail here rather than
pass silently.

The shapes below are AWS's published ones (checked against `describe-instance-types` on
2026-11-18). A size that is not in the table fails the test, so a new size cannot slip in without
its shape being looked up and written down here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NODE = ROOT / "infra" / "modules" / "node"
ENVS = ROOT / "infra" / "envs"

# size -> (vCPUs, memory in GiB); all Graviton (arm64), which is what the images are built for.
SHAPES: dict[str, tuple[int, int]] = {
    "t4g.medium": (2, 4),
    "t4g.large": (2, 8),
    "c6g.large": (2, 4),
    "c7g.large": (2, 4),
    "m6g.medium": (1, 4),
    "m6g.large": (2, 8),
    "m7g.medium": (1, 4),
    "m7g.large": (2, 8),
}
FIRST = "t4g.medium"


def uncommented(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def default_sizes(variables_tf: str, name: str) -> list[str]:
    """The default list of a variable, read from its text."""
    block = re.search(rf'variable "{name}" \{{(.*?)\n\}}', variables_tf, re.S)
    if block is None:
        return []
    found = re.search(r"default\s*=\s*\[([^\]]*)\]", block.group(1))
    return re.findall(r'"([^"]+)"', found.group(1)) if found else []


def size_problems(sizes: list[str]) -> list[str]:
    """Why this list of sizes is not safe to run the node on (empty list: it is)."""
    problems: list[str] = []
    if len(sizes) < 2:
        problems.append(
            "one size is a single point of failure: it needs a second one to fall back to"
        )
    if len(set(sizes)) != len(sizes):
        problems.append("a size is listed twice")
    for size in sizes:
        if size not in SHAPES:
            problems.append(
                f"{size} is not in the shape table (look it up, then write it down in this test)"
            )
    if sizes and sizes[0] != FIRST:
        problems.append(f"the first size must stay {FIRST}, the normal choice")
    known = [SHAPES[s] for s in sizes if s in SHAPES]
    if known and any(shape != SHAPES.get(sizes[0]) for shape in known):
        problems.append("every size must be the same shape as the first (CPUs and memory)")
    return problems


def policy_problems(main_tf: str) -> list[str]:
    """The server group's side: a prioritised, On-Demand-by-default policy built from the list."""
    text = re.sub(r"\s+", " ", uncommented(main_tf))
    problems: list[str] = []
    if "mixed_instances_policy" not in text:
        problems.append("the group has no mixed_instances_policy")
    if 'on_demand_allocation_strategy = "prioritized"' not in text:
        problems.append(
            'the order must be "prioritized", else the fallback size becomes the normal one'
        )
    if "on_demand_percentage_above_base_capacity = var.spot ? 0 : 100" not in text:
        problems.append("without var.spot the group must be 100 percent On-Demand (ADR-0028)")
    if "for_each = var.instance_types" not in text:
        problems.append("the group's overrides must be built from var.instance_types")
    if "instance_market_options" in text:
        problems.append("a spot block in the launch template is refused beside a list of sizes")
    return problems


# ---------- the real files ----------


def test_the_module_lists_two_equivalent_graviton_sizes() -> None:
    sizes = default_sizes((NODE / "variables.tf").read_text(encoding="utf-8"), "instance_types")
    assert sizes, "the module has no instance_types default"
    assert size_problems(sizes) == []
    assert sizes[:2] == ["t4g.medium", "c6g.large"]


def test_the_group_prefers_the_first_size_and_is_on_demand() -> None:
    assert policy_problems((NODE / "main.tf").read_text(encoding="utf-8")) == []


def test_the_launch_template_uses_the_first_size_of_the_list() -> None:
    text = uncommented((NODE / "main.tf").read_text(encoding="utf-8"))
    assert "instance_type = var.instance_types[0]" in text


def test_nothing_still_uses_the_old_single_size_variable() -> None:
    for tf in list(ENVS.glob("*/*.tf")) + list(NODE.glob("*.tf")):
        text = uncommented(tf.read_text(encoding="utf-8"))
        assert not re.search(r"var\.instance_type", text), f"{tf} still uses var.instance_type"
    assert 'variable "instance_type"' not in (NODE / "variables.tf").read_text(encoding="utf-8")


def test_staging_defaults_to_the_same_list_as_the_module() -> None:
    module = default_sizes((NODE / "variables.tf").read_text(encoding="utf-8"), "instance_types")
    staging = default_sizes(
        (ENVS / "staging" / "variables.tf").read_text(encoding="utf-8"), "node_instance_types"
    )
    assert staging == module
    main = uncommented((ENVS / "staging" / "main.tf").read_text(encoding="utf-8"))
    assert "instance_types        = var.node_instance_types" in main


def test_production_takes_the_modules_list_unchanged() -> None:
    main = uncommented((ENVS / "prod" / "main.tf").read_text(encoding="utf-8"))
    assert "instance_types" not in main, "production should keep the module's list, not its own"


# ---------- the checkers refuse broken input ----------


@pytest.mark.parametrize(
    ("sizes", "expect"),
    [
        (["t4g.medium"], "single point of failure"),
        (["t4g.medium", "t4g.medium"], "twice"),
        (["t4g.medium", "t3.medium"], "not in the shape table"),
        (["t4g.medium", "m6g.medium"], "same shape"),
        (["t4g.medium", "t4g.large"], "same shape"),
        (["c6g.large", "t4g.medium"], "must stay t4g.medium"),
        ([], "single point of failure"),
    ],
)
def test_the_size_checker_refuses_a_bad_list(sizes: list[str], expect: str) -> None:
    assert any(expect in p for p in size_problems(sizes)), size_problems(sizes)


def test_the_size_checker_accepts_the_real_list() -> None:
    assert size_problems(["t4g.medium", "c6g.large"]) == []
    assert size_problems(["t4g.medium", "c6g.large", "c7g.large"]) == []


def test_the_policy_checker_refuses_the_old_group() -> None:
    old = 'resource "aws_autoscaling_group" "node" {\n  launch_template {\n    id = x\n  }\n}\n'
    assert policy_problems(old)
    spot_in_template = (NODE / "main.tf").read_text(
        encoding="utf-8"
    ) + '\n  dynamic "instance_market_options" {}\n'
    assert any("spot block" in p for p in policy_problems(spot_in_template))
    not_prioritised = (
        (NODE / "main.tf").read_text(encoding="utf-8").replace('"prioritized"', '"lowest-price"')
    )
    assert any("prioritized" in p for p in policy_problems(not_prioritised))
    no_ondemand = (NODE / "main.tf").read_text(encoding="utf-8").replace("var.spot ? 0 : 100", "0")
    assert any("On-Demand" in p for p in policy_problems(no_ondemand))
