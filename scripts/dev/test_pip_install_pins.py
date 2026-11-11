"""Every `pip install` in a service image is checked by hash, even Kaval's own code (KAV-111).

The libraries were always installed with `--require-hashes`. Kaval's own code was installed with
`pip install --no-deps .`, and `pip` built it with a packing tool (hatchling) that it fetched
from PyPI on the spot, together with the tool's own helpers, none of it hash-checked. The fix
(ADR-0086, Lab 88) is a separate `wheel` stage: the packing tool comes from uv.lock by hash, the
wheel is built without fetching anything, and the runtime image installs only that finished file.

Nothing here builds an image (CI's image job does, for real, on every pull request). These tests
read the files and stop the quiet ways it can slip back: a Dockerfile going back to
`pip install .`, an install with no hash, a build that is allowed to fetch its own tool again,
the pin of the packing tool disagreeing with the lock, or the wheel stage on another base image
than the runtime one.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SERVICES = ["gateway", "collector", "agent", "executor"]
DOCKERFILES = {name: ROOT / "services" / name / "Dockerfile" for name in SERVICES}
PYPROJECT = ROOT / "pyproject.toml"
LOCK = ROOT / "uv.lock"


def _instructions(text: str) -> list[str]:
    """The Dockerfile's instructions: continued lines joined, comments and blanks dropped."""
    out: list[str] = []
    pending = ""
    for line in text.splitlines():
        if not pending and (not line.strip() or line.lstrip().startswith("#")):
            continue
        if line.rstrip().endswith("\\"):
            pending += line.rstrip()[:-1].strip() + " "
            continue
        out.append((pending + line.strip()).strip())
        pending = ""
    assert not pending, "a line continuation that never ends"
    return out


def _stages(text: str) -> dict[str, list[str]]:
    """Instructions by stage name (the `AS name`; the last, unnamed stage is called `runtime`)."""
    stages: dict[str, list[str]] = {}
    current = ""
    for instruction in _instructions(text):
        if instruction.startswith("FROM "):
            named = re.search(r"\sAS\s+(\S+)\s*$", instruction)
            current = named.group(1) if named else "runtime"
            stages[current] = []
        if current:
            stages[current].append(instruction)
    return stages


def unhashed_installs(text: str) -> list[str]:
    """Every `pip install` that is not checked by hash.

    One is checked when it says `--require-hashes`, or when everything it installs is a finished
    `.whl` file (a wheel is built by the stage before it, never fetched). Anything else installs
    something nobody fingerprinted, a directory (`.`), a name, an editable link.
    """
    bad: list[str] = []
    for instruction in _instructions(text):
        if not instruction.startswith("RUN "):
            continue
        for command in re.split(r"&&|;", instruction[len("RUN ") :]):
            words = command.split()
            if words[:2] != ["pip", "install"]:
                continue
            if "--require-hashes" in words:
                continue
            targets = [w for w in words[2:] if not w.startswith("-")]
            if targets and all(t.endswith(".whl") for t in targets):
                continue
            bad.append(command.strip())
    return bad


# --- The rule itself, including the cases it must refuse -------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "RUN pip install --no-deps . && pip uninstall -y pip",  # the old line, KAV-111
        "RUN pip install --no-deps -e .",  # an editable link satisfies a scanner, not a hash
        "RUN pip install hatchling==1.33.0",  # a name and a version is not a fingerprint
        "RUN pip install -r /tmp/requirements.txt",  # a requirements file without --require-hashes
        "RUN pip install --no-deps /wheels/kaval.whl pytest",  # a wheel plus something unchecked
    ],
)
def test_the_rule_refuses_an_install_nobody_fingerprinted(line: str) -> None:
    assert unhashed_installs(line), f"should have been refused: {line}"


@pytest.mark.parametrize(
    "line",
    [
        "RUN pip install --require-hashes -r /tmp/requirements.txt && rm /tmp/requirements.txt",
        "RUN pip install --no-deps /wheels/*.whl && rm -rf /wheels",
        # builds a wheel, installs nothing:
        "RUN pip wheel --no-deps --no-build-isolation --wheel-dir /wheels .",
        "RUN pip uninstall -y setuptools wheel pip",
    ],
)
def test_the_rule_accepts_the_checked_forms(line: str) -> None:
    assert unhashed_installs(line) == []


# --- The four real Dockerfiles ---------------------------------------------------------------


@pytest.mark.parametrize("name", SERVICES)
def test_every_install_in_the_image_is_checked_by_hash(name: str) -> None:
    text = DOCKERFILES[name].read_text(encoding="utf-8")
    assert unhashed_installs(text) == [], f"{name}: pip install with no hash check"


@pytest.mark.parametrize("name", SERVICES)
def test_the_wheel_is_built_from_hash_checked_tools_and_fetches_nothing(name: str) -> None:
    stages = _stages(DOCKERFILES[name].read_text(encoding="utf-8"))
    assert "wheel" in stages, f"{name}: no `wheel` stage"
    runs = [i for i in stages["wheel"] if i.startswith("RUN ")]
    tool_install = [i for i, r in enumerate(runs) if "pip install --require-hashes" in r]
    build = [i for i, r in enumerate(runs) if "pip wheel" in r]
    assert tool_install and build, f"{name}: the wheel stage must install the tool, then build"
    assert tool_install[0] < build[0], f"{name}: the tool must be installed before the build"
    assert "--no-build-isolation" in runs[build[0]], (
        f"{name}: without --no-build-isolation pip fetches its own, unchecked, packing tool"
    )
    assert "--no-deps" in runs[build[0]], f"{name}: the build must not fetch libraries"
    assert any("--only-group build" in i for i in stages["lock"]), (
        f"{name}: the lock stage must export the build group, with hashes"
    )
    assert any("COPY --from=lock /build-requirements.txt" in i for i in stages["wheel"])


@pytest.mark.parametrize("name", SERVICES)
def test_the_runtime_image_installs_the_wheel_and_keeps_no_packing_tool(name: str) -> None:
    stages = _stages(DOCKERFILES[name].read_text(encoding="utf-8"))
    runtime = stages["runtime"]
    assert any(i.startswith("COPY --from=wheel /wheels") for i in runtime)
    assert any("pip install --no-deps /wheels/" in i for i in runtime)
    assert not any("hatchling" in i or "pip wheel" in i for i in runtime)


@pytest.mark.parametrize("name", SERVICES)
def test_the_wheel_stage_uses_the_same_base_image_as_the_runtime_image(name: str) -> None:
    stages = _stages(DOCKERFILES[name].read_text(encoding="utf-8"))
    wheel_base = stages["wheel"][0].split()[1]
    runtime_base = stages["runtime"][0].split()[1]
    assert re.search(r"@sha256:[0-9a-f]{64}$", wheel_base), "base image not pinned by digest"
    assert wheel_base == runtime_base, f"{name}: the wheel is built on another base than it runs on"


# --- One version of the packing tool, everywhere ---------------------------------------------


def _pinned(requirement: str) -> str:
    found = re.fullmatch(r"hatchling==(\d+(?:\.\d+)*)", requirement.replace(" ", ""))
    assert found, f"hatchling must be pinned exactly, got {requirement!r}"
    return found.group(1)


def test_the_packing_tool_has_one_version_in_pyproject_and_in_the_lock() -> None:
    pyproject = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    (required,) = [r for r in pyproject["build-system"]["requires"] if "hatchling" in r]
    (grouped,) = [r for r in pyproject["dependency-groups"]["build"] if "hatchling" in r]
    locked = [
        p["version"]
        for p in tomllib.loads(LOCK.read_text(encoding="utf-8"))["package"]
        if p["name"] == "hatchling"
    ]
    assert locked, "uv.lock does not contain hatchling: run `make lock`"
    assert _pinned(required) == _pinned(grouped) == locked[0], (
        "the build-system pin, the `build` group and uv.lock disagree on hatchling's version"
    )


def test_the_build_group_is_not_installed_by_default() -> None:
    pyproject = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    defaults = pyproject.get("tool", {}).get("uv", {}).get("default-groups", ["dev"])
    assert "build" not in defaults, "the packing tool belongs in the wheel stage, not in every sync"
