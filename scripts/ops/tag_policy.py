"""The tag policy (KAV-93, ADR-0061): which labels every Kaval resource must carry, and the checks.

One place for the rules, so the offline check that runs in CI, the scan of the live account
(`tag_scan.py`) and the cost report (`showback.py`) can never disagree about what "labelled" means.

Everything here is a pure function over text or plain dicts: no AWS call, no file written.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PROJECT = "kaval"
REQUIRED_KEYS = ("Project", "Env", "ManagedBy")
ENVS = ("prod", "staging", "dev", "state")
# terraform: Terraform makes it and would make it again. kaval-lambda: one of Kaval's own jobs
# made it (a pre-stop snapshot), so deleting it is final. manual: a person made it by hand.
MANAGED_BY = ("terraform", "kaval-lambda", "manual")
ALLOWED = {"Project": (PROJECT,), "Env": ENVS, "ManagedBy": MANAGED_BY}


# ── the live account ─────────────────────────────────────────────────────────────────────────────


def is_kaval(tags: Mapping[str, str]) -> bool:
    """Kaval's own, or something that has to be looked at as if it were.

    A resource with no Project tag at all counts as Kaval's: the scan must not wave through a
    forgotten Kaval resource just because it lost its label. Another project's resources
    (stock-trader shares this account) say so with a different Project value and are left alone.
    """
    return tags.get("Project", PROJECT) == PROJECT


def tag_problems(tags: Mapping[str, str]) -> list[str]:
    """What is wrong with one Kaval resource's tags (empty = fine)."""
    problems = []
    for key in REQUIRED_KEYS:
        value = tags.get(key)
        if value is None:
            problems.append(f"missing {key}")
        elif value not in ALLOWED[key]:
            problems.append(f"{key}={value} is not one of {', '.join(ALLOWED[key])}")
    return problems


# ── the Terraform and job code (offline, for CI) ─────────────────────────────────────────────────


def _code(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def default_tags_problems(env: str, main_tf: str) -> list[str]:
    """One environment's provider-wide default tags."""
    m = re.search(r"default_tags\s*\{\s*tags\s*=\s*\{(.*?)\}", _code(main_tf), re.S)
    if not m:
        return [f"{env}: no default_tags block (nothing Terraform makes would be labelled)"]
    found = dict(re.findall(r'(\w+)\s*=\s*"([^"]*)"', m.group(1)))
    problems = []
    expected = {"Project": PROJECT, "Env": env, "ManagedBy": "terraform"}
    for key, want in expected.items():
        if key not in found:
            problems.append(f"{env}: default_tags has no {key}")
        elif found[key] != want:
            problems.append(f"{env}: default_tags {key} is {found[key]!r}, must be {want!r}")
    return problems


def node_problems(node_tf: str) -> list[str]:
    """The auto-scaling group's servers and disks.

    Terraform's default tags do not reach what an Auto Scaling group launches (found live
    2026-10-18), so the node module has to write the three keys itself, three times: on the
    instance, on its disk, and on the group.
    """
    code = _code(node_tf)
    problems = []
    local = re.search(r"required_tags\s*=\s*\{(.*?)\}", code, re.S)
    keys = set(re.findall(r"(\w+)\s*=", local.group(1))) if local else set()
    for key in REQUIRED_KEYS:
        if key not in keys:
            problems.append(f"node: local required_tags has no {key}")
    for kind in ("instance", "volume"):
        spec = re.search(
            rf'tag_specifications\s*\{{\s*resource_type\s*=\s*"{kind}"(.*?)\n  \}}', code, re.S
        )
        if not spec or "local.required_tags" not in spec.group(1):
            problems.append(f"node: the launch template does not tag the {kind} with required_tags")
    group = re.search(r'resource "aws_autoscaling_group" "node".*?\n\}', code, re.S)
    if not group or not re.search(
        r'dynamic "tag"\s*\{\s*for_each\s*=\s*local\.required_tags', group.group(0), re.S
    ):
        problems.append("node: the auto-scaling group does not carry required_tags")
    return problems


def job_problems(name: str, source: str) -> list[str]:
    """A Kaval job that makes snapshots: the snapshot's tag list must name every required key."""
    spec = re.search(r"TagSpecifications=\[\s*\{(.*?)\}\s*\],?\s*\n\s*\)", source, re.S)
    if not spec:
        return [f"{name}: no TagSpecifications on its snapshot (the snapshot would be unlabelled)"]
    return [
        f"{name}: its snapshot is not given {key}"
        for key in REQUIRED_KEYS
        if f'"Key": "{key}"' not in spec.group(1)
    ]


def repo_problems(root: Path = ROOT) -> list[str]:
    """Every rule, over the real repository."""
    problems: list[str] = []
    envs = root / "infra" / "envs"
    for folder in sorted(p for p in envs.iterdir() if p.is_dir() and (p / "main.tf").exists()):
        if folder.name in ENVS:
            text = (folder / "main.tf").read_text(encoding="utf-8")
            problems += default_tags_problems(folder.name, text)
    problems += node_problems((root / "infra/modules/node/main.tf").read_text(encoding="utf-8"))
    for name, rel in (
        ("idle_stop", "infra/modules/idle-stop/lambda/idle_stop.py"),
        ("hard_stop", "infra/modules/budget/lambda/hard_stop.py"),
        ("waste_removal", "infra/modules/waste-removal/lambda/waste_removal.py"),
    ):
        problems += job_problems(name, (root / rel).read_text(encoding="utf-8"))
    return problems


def main(argv: Iterable[str] = ()) -> int:
    problems = repo_problems()
    for p in problems:
        print("TAG POLICY:", p)
    if not problems:
        print("tag policy: environments, server module and both stop jobs label what they make")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
