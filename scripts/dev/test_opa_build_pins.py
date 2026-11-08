"""The OPA in the agent and executor images is built from source, the same way in both (KAV-132).

Stage 1b of services/agent/Dockerfile and services/executor/Dockerfile compiles OPA from one
pinned commit, in a Go image pinned by digest, with a list of raised libraries and an expected
checksum (ADR-0079, Lab 80). Nothing here builds it: that takes minutes, and the image job in
ci.yml does it for real on every pull request. These tests read the files and stop the quiet
ways the pins can fail: one Dockerfile bumped and not the other, a pin that stopped being exact,
a checksum left as a placeholder or checked in the same step as the compile, the runner's own
OPA at another version, and the CI image check no longer reading its expectations from the
Dockerfile.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / "services" / "agent" / "Dockerfile"
EXECUTOR = ROOT / "services" / "executor" / "Dockerfile"
DOCKERFILES = [AGENT, EXECUTOR]
CI = ROOT / ".github" / "workflows" / "ci.yml"
DEPENDABOT = ROOT / ".github" / "dependabot.yml"

GO_IMAGE = r"FROM --platform=\$BUILDPLATFORM golang:\d+\.\d+\.\d+-[a-z]+@sha256:[0-9a-f]{64} AS opa"


def _stage(dockerfile: Path) -> list[str]:
    """The `opa` stage's instructions: continued lines joined, comments and blanks dropped."""
    lines = dockerfile.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if re.match(r"FROM .* AS opa\s*$", line))
    following = range(start + 1, len(lines))
    end = next((j for j in following if lines[j].startswith("FROM ")), len(lines))
    instructions: list[str] = []
    pending = ""
    for line in lines[start:end]:
        if not pending and (not line.strip() or line.lstrip().startswith("#")):
            continue
        if line.rstrip().endswith("\\"):
            pending += line.rstrip()[:-1].strip() + " "
            continue
        instructions.append((pending + line.strip()).strip())
        pending = ""
    assert not pending, f"{dockerfile.parent.name}: a line continuation that never ends"
    return instructions


def _arg(stage: list[str], name: str) -> str:
    for instruction in stage:
        found = re.fullmatch(rf'ARG {name}=("?)(.*)\1', instruction)
        if found:
            return found.group(2)
    raise AssertionError(f"no `ARG {name}=` in the opa stage")


def _step(stage: list[str], *, containing: str) -> int:
    """Where in the stage the one RUN that contains `containing` is."""
    hits = [i for i, ins in enumerate(stage) if ins.startswith("RUN ") and containing in ins]
    assert len(hits) == 1, f"expected exactly one RUN containing {containing!r}, found {len(hits)}"
    return hits[0]


by_service = pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=lambda p: p.parent.name)


def test_both_images_build_opa_with_identical_instructions() -> None:
    agent, executor = _stage(AGENT), _stage(EXECUTOR)
    assert agent, "the agent's Dockerfile has no `AS opa` stage"
    assert agent == executor, "the opa stage differs between the two Dockerfiles: bump both"


@by_service
def test_the_go_toolchain_is_pinned_by_digest(dockerfile: Path) -> None:
    first = _stage(dockerfile)[0]
    assert re.fullmatch(GO_IMAGE, first), f"an exact Go version pinned by digest, got: {first}"


@by_service
def test_the_source_is_one_exact_commit_that_is_checked_and_tagged(dockerfile: Path) -> None:
    stage = _stage(dockerfile)
    assert re.fullmatch(r"\d+\.\d+\.\d+", _arg(stage, "OPA_VERSION"))
    assert re.fullmatch(r"[0-9a-f]{40}", _arg(stage, "OPA_COMMIT"))
    compile_step = stage[_step(stage, containing="go build")]
    for needed in (
        'git fetch -q --depth 1 https://github.com/open-policy-agent/opa.git "${OPA_COMMIT}"',
        'test "$(git rev-parse HEAD)" = "${OPA_COMMIT}"',
        'git tag "v${OPA_VERSION}"',
    ):
        assert needed in compile_step, f"the compile step lost: {needed}"
    # Without the tag Go stamps v0.0.0-<date>-<commit>; scanners then call OPA itself old.
    assert compile_step.index("git tag") < compile_step.index("go build")


@by_service
def test_every_override_is_an_exact_library_version(dockerfile: Path) -> None:
    """An empty list is allowed (the day OPA catches up); a floating one is not."""
    stage = _stage(dockerfile)
    for spec in _arg(stage, "OPA_GO_GET").split():
        assert re.fullmatch(r"[\w.\-/]+@v\d+\.\d+\.\d+", spec), f"want module@vX.Y.Z, got {spec!r}"
    assert "go get ${OPA_GO_GET}" in stage[_step(stage, containing="go build")]
    # Each override is looked up in the finished binary, so a `go get` that changed nothing stops.
    assert "go version -m /opa" in stage[_step(stage, containing="sha256sum")]


@by_service
def test_the_result_is_checksummed_in_its_own_step_after_the_compile(dockerfile: Path) -> None:
    stage = _stage(dockerfile)
    compile_at = _step(stage, containing="go build")
    check_at = _step(stage, containing="sha256sum")
    arg_at = next(i for i, ins in enumerate(stage) if ins.startswith("ARG OPA_SHA256_ARM64="))
    assert compile_at < arg_at < check_at, "declare the checksum after the compile, use it later"
    assert "OPA_SHA256_ARM64" not in stage[compile_at], "a new checksum would recompile the binary"
    expected = _arg(stage, "OPA_SHA256_ARM64")
    assert re.fullmatch(r"[0-9a-f]{64}", expected), "OPA_SHA256_ARM64 must be a sha256"
    assert set(expected) != {"0"}, "OPA_SHA256_ARM64 is still the placeholder"


@by_service
def test_the_stage_refuses_to_build_for_anything_but_arm64(dockerfile: Path) -> None:
    """CLAUDE.md constraint 2. The old download stage fetched the arm64 file whatever the target."""
    stage = _stage(dockerfile)
    assert 'test "${TARGETARCH}" = arm64' in stage[_step(stage, containing="go build")]


@by_service
def test_the_runtime_image_copies_the_binary_from_the_stage(dockerfile: Path) -> None:
    lines = dockerfile.read_text(encoding="utf-8").splitlines()
    assert "COPY --from=opa /opa /usr/local/bin/opa" in lines


def test_the_runners_own_opa_is_the_same_version_as_the_images() -> None:
    """ci.yml runs `opa test` and pytest against OPA's own release: the version the images build."""
    found = re.search(r'(?m)^\s+OPA_VERSION:\s*"([^"]+)"', CI.read_text(encoding="utf-8"))
    assert found, "ci.yml has no OPA_VERSION"
    assert found.group(1) == _arg(_stage(AGENT), "OPA_VERSION")


def test_the_ci_image_check_reads_its_expectations_from_the_dockerfile() -> None:
    text = CI.read_text(encoding="utf-8")
    for needle in (
        "sed -n 's/^ARG OPA_VERSION=//p'",
        "sed -n 's/^ARG OPA_COMMIT=//p'",
        "Version: ${want_version}",
        "Build Commit: ${want_commit}",
        "Go Version: go${want_go}",
    ):
        assert needle in text, f"ci.yml's image check no longer asserts: {needle}"


def test_dependabot_holds_back_go_minor_and_major_versions() -> None:
    config = yaml.safe_load(DEPENDABOT.read_text(encoding="utf-8"))
    docker = next(u for u in config["updates"] if u["package-ecosystem"] == "docker")
    assert {"/services/agent", "/services/executor"} <= set(docker["directories"])
    rules = {rule["dependency-name"]: set(rule["update-types"]) for rule in docker["ignore"]}
    assert rules.get("golang") == {"version-update:semver-minor", "version-update:semver-major"}
