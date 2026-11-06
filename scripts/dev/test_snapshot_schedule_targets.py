"""Each environment's snapshot schedule backs up its own database disk, and only that (KAV-128).

AWS's Data Lifecycle Manager covers a disk that has ANY ONE of a schedule's target tags, not all
of them. The module asked for `Role = database-data` as well as the disk's `Name`, and every
environment's disk carries that same `Role`, so staging's schedule copied production's disk too
(seen on 2026-11-04). These tests read the real Terraform files, work out the tags each
environment's disk really carries (the module's own tags plus the environment's default tags), and
apply AWS's rule both ways: a schedule must cover its own disk (or there would be no backups at
all, which is worse than the bug), and it must cover no other environment's disk.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "infra" / "modules" / "database" / "main.tf"

Tags = dict[str, str]


def _search(pattern: str, text: str) -> re.Match[str]:
    found = re.search(pattern, text)
    assert found is not None, f"expected to find {pattern!r}"
    return found


def _code(text: str) -> str:
    """The Terraform without its full-line comments, which may mention anything."""
    return re.sub(r"(?m)^\s*#.*$", "", text)


def _body(text: str, header: str) -> str:
    """What is between the braces of the first block that starts with `header` (braces matched)."""
    opening = text.index("{", text.index(header))
    depth = 0
    for at in range(opening, len(text)):
        depth += {"{": 1, "}": -1}.get(text[at], 0)
        if depth == 0:
            return text[opening + 1 : at]
    raise AssertionError(f"no closing brace for {header!r}")


def _map(text: str, key: str) -> Tags:
    """The `Key = "value"` pairs of the first `key = { ... }` in `text`."""
    inner = _body(text, _search(rf"\b{key}\s*=\s*\{{", text).group(0))
    return dict(re.findall(r'^\s*([A-Za-z]+)\s*=\s*"([^"]*)"\s*$', inner, re.M))


def _render(tags: Tags, prefix: str) -> Tags:
    out = {k: v.replace("${var.name_prefix}", prefix) for k, v in tags.items()}
    assert not any("${" in v for v in out.values()), f"a placeholder not filled here: {out}"
    return out


def _prefix(module_block: str, environment: str) -> str:
    """The `name_prefix` an environment gives the database module, literal or through a local."""
    given = _search(r"name_prefix\s*=\s*(.+)", module_block).group(1).strip()
    if given.startswith('"'):
        return given.strip('"')
    assert given == "local.name_prefix", f"an expression this test does not follow: {given}"
    return _search(r'name_prefix\s*=\s*"([^"]+)"', _body(environment, "locals")).group(1)


def covers(schedule_tags: Tags, disk_tags: Tags) -> bool:
    """AWS's rule: one matching tag is enough."""
    return any(disk_tags.get(key) == value for key, value in schedule_tags.items())


def _environments() -> dict[str, dict[str, Tags]]:
    """Every environment that builds the database module: the tags of its schedule and its disk."""
    module = _code(MODULE.read_text(encoding="utf-8"))
    disk_in_module = _map(_body(module, 'resource "aws_ebs_volume" "data"'), "tags")
    schedule_in_module = _map(
        _body(module, 'resource "aws_dlm_lifecycle_policy" "database"'), "target_tags"
    )
    found: dict[str, dict[str, Tags]] = {}
    for main in sorted((ROOT / "infra" / "envs").glob("*/main.tf")):
        text = _code(main.read_text(encoding="utf-8"))
        if 'source = "../../modules/database"' not in text:
            continue
        prefix = _prefix(_body(text, 'module "database"'), text)
        defaults = _map(_body(text, "default_tags"), "tags")  # put on every resource
        found[main.parent.name] = {
            "schedule": _render(schedule_in_module, prefix),
            "disk": {**_render(disk_in_module, prefix), **defaults},
        }
    return found


ENVIRONMENTS = _environments()


def test_production_and_staging_are_both_read() -> None:
    """Without this, the loops below could pass by checking nothing."""
    assert {"prod", "staging"} <= set(ENVIRONMENTS)
    for name, tags in ENVIRONMENTS.items():
        assert tags["disk"]["Env"] == name, (name, tags["disk"])
        assert tags["schedule"], name


@pytest.mark.parametrize("environment", sorted(ENVIRONMENTS))
def test_each_schedule_covers_its_own_database_disk(environment: str) -> None:
    mine = ENVIRONMENTS[environment]
    assert covers(mine["schedule"], mine["disk"]), (
        f"{environment}'s schedule {mine['schedule']} matches none of its own disk's tags "
        f"{mine['disk']}: it would make no backups"
    )


@pytest.mark.parametrize(
    ("environment", "other"),
    [(a, b) for a in sorted(ENVIRONMENTS) for b in sorted(ENVIRONMENTS) if a != b],
)
def test_no_schedule_covers_another_environments_disk(environment: str, other: str) -> None:
    mine, theirs = ENVIRONMENTS[environment], ENVIRONMENTS[other]
    shared = {k: v for k, v in mine["schedule"].items() if theirs["disk"].get(k) == v}
    assert not covers(mine["schedule"], theirs["disk"]), (
        f"{environment}'s schedule would also back up {other}'s disk, because {other}'s disk "
        f"carries {shared} and AWS covers a disk that has any one of a schedule's tags"
    )


def test_the_check_does_see_the_old_mistake() -> None:
    """The control: the module as it was before the fix (Role as well as Name) is caught by the
    same rule, so a pass above means something."""
    old = {"Role": "database-data", "Name": "kaval-prod-database-data"}
    assert covers(old, ENVIRONMENTS["prod"]["disk"])  # it did cover its own disk
    assert covers(old, ENVIRONMENTS["staging"]["disk"])  # and staging's too, which is the bug
