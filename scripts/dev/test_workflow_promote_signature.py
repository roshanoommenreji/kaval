"""promote.yml checks every image's signature before it opens anything (KAV-139, ADR-0092).

promote.yml can only be run for real on `main`, so these tests read the workflow file and keep the
properties in place that make the check mean something: the signing tool is fetched at a fixed
version and refused if its checksum differs, before any AWS access; the runner is logged in to the
registry before the gate runs; the gate reads the registry address from that login and not from a
file; the signer is never a pattern; and the signer the gate asks for is word for word the signer
release.yml signs as. `problems(doc)` is run on the real workflow and on made-up copies that break
each rule, so the check is shown to catch what it is for.
"""

from __future__ import annotations

import copy
import re
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "release"))
import promote  # noqa: E402

PROMOTE = ROOT / ".github" / "workflows" / "promote.yml"
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
SHA256_RE = r"^[0-9a-f]{64}$"


def load(path: Path) -> dict[str, Any]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(doc, dict)
    return doc


def steps_of(doc: dict[str, Any], job: str = "gate") -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = doc["jobs"][job]["steps"]
    return steps


def index(steps: list[dict[str, Any]], pred: Any) -> int:
    for i, s in enumerate(steps):
        if pred(s):
            return i
    return -1


def is_install(s: dict[str, Any]) -> bool:
    return "cosign" in str(s.get("name", "")) and "install" in str(s.get("name", ""))


def is_login(s: dict[str, Any]) -> bool:
    return str(s.get("uses", "")).startswith("aws-actions/configure-aws-credentials@")


def is_ecr(s: dict[str, Any]) -> bool:
    return str(s.get("uses", "")).startswith("aws-actions/amazon-ecr-login@")


def is_verify(s: dict[str, Any]) -> bool:
    return s.get("name") == "verify"


def problems(doc: dict[str, Any]) -> list[str]:
    """Everything about the signature check that is wrong in this workflow document."""
    found: list[str] = []
    steps = steps_of(doc)
    install, login, ecr, verify = (
        index(steps, is_install),
        index(steps, is_login),
        index(steps, is_ecr),
        index(steps, is_verify),
    )
    if min(install, login, ecr, verify) < 0:
        return ["the gate job lacks the install, login, registry login or verify step"]
    if not install < login:
        found.append("cosign is installed after AWS access")
    if not login < ecr < verify:
        found.append("the registry login is not between the AWS login and verify")
    run = str(steps[install].get("run", ""))
    env = doc.get("env", {})
    if "cosign-linux-amd64" not in run:
        found.append("the x64 runner does not fetch the linux-amd64 cosign")
    if "sha256sum --check --strict" not in run or "${COSIGN_SHA256}" not in run:
        found.append("the download is not checked against a checksum")
    if "COSIGN_VERSION" not in run:
        found.append("the cosign version is not the pinned one")
    if not re.match(SHA256_RE, str(env.get("COSIGN_SHA256", ""))):
        found.append("COSIGN_SHA256 is not a SHA-256")
    venv = steps[verify].get("env", {})
    if "steps.ecr.outputs.registry" not in str(venv.get("REGISTRY", "")):
        found.append("verify does not take the registry from the registry login")
    if steps[ecr].get("id") != "ecr":
        found.append("the registry login step is not called ecr")
    if not str(venv.get("COSIGN", "")).endswith("/cosign"):
        found.append("verify does not point at the installed cosign")
    if "github.token" not in str(venv.get("GH_TOKEN", "")):
        found.append("verify has no token for gh")
    if "regexp" in yaml.safe_dump(doc).lower():
        found.append("a pattern appears in the workflow")
    # The gate may read and sign in; it may never write to the repository, and propose may not
    # reach AWS.
    perms = doc["jobs"]["gate"]["permissions"]
    if set(perms) - {"id-token", "contents"} or perms.get("contents") != "read":
        found.append(f"the gate job has more power than it needs: {perms}")
    if "id-token" in doc["jobs"]["propose"]["permissions"]:
        found.append("propose can reach AWS")
    return found


PROMOTE_TEXT = PROMOTE.read_text(encoding="utf-8")
PROMOTE_DOC = load(PROMOTE)


def test_the_real_workflow_has_no_problems() -> None:
    assert problems(PROMOTE_DOC) == []


# ── the made-up workflows that break one rule each ───────────────────────────────────


def broken(change: Any) -> dict[str, Any]:
    doc = copy.deepcopy(PROMOTE_DOC)
    change(doc)
    return doc


def test_cosign_installed_after_aws_access_is_caught() -> None:
    def move(doc: dict[str, Any]) -> None:
        steps = steps_of(doc)
        steps.append(steps.pop(index(steps, is_install)))
        steps.insert(index(steps, is_verify), steps.pop())  # still before verify, after the login

    assert any("after AWS access" in p for p in problems(broken(move)))


def test_a_download_without_a_checksum_is_caught() -> None:
    def drop(doc: dict[str, Any]) -> None:
        step = steps_of(doc)[index(steps_of(doc), is_install)]
        step["run"] = step["run"].replace("sha256sum --check --strict", "true")

    assert any("checksum" in p for p in problems(broken(drop)))


def test_the_arm_tool_on_the_x64_runner_is_caught() -> None:
    def swap(doc: dict[str, Any]) -> None:
        step = steps_of(doc)[index(steps_of(doc), is_install)]
        step["run"] = step["run"].replace("amd64", "arm64")

    assert any("linux-amd64" in p for p in problems(broken(swap)))


def test_a_verify_step_without_the_registry_is_caught() -> None:
    def drop(doc: dict[str, Any]) -> None:
        del steps_of(doc)[index(steps_of(doc), is_verify)]["env"]["REGISTRY"]

    assert any("registry" in p for p in problems(broken(drop)))


def test_a_gate_that_may_write_to_the_repository_is_caught() -> None:
    def widen(doc: dict[str, Any]) -> None:
        doc["jobs"]["gate"]["permissions"]["contents"] = "write"

    assert any("more power" in p for p in problems(broken(widen)))


def test_a_propose_job_that_can_reach_aws_is_caught() -> None:
    def widen(doc: dict[str, Any]) -> None:
        doc["jobs"]["propose"]["permissions"]["id-token"] = "write"

    assert any("propose can reach AWS" in p for p in problems(broken(widen)))


# ── one signer, written once in each file, and the two agree ─────────────────────────


def test_the_gate_asks_for_the_signer_release_signs_as() -> None:
    env = load(RELEASE)["env"]
    assert promote.SIGNER_IDENTITY == env["SIGNER_IDENTITY"]
    assert promote.SIGNER_ISSUER == env["SIGNER_ISSUER"]
    assert promote.SIGNER_IDENTITY.endswith("/.github/workflows/release.yml@refs/heads/main")


def test_the_gate_uses_the_same_cosign_as_release() -> None:
    release, gate = load(RELEASE)["env"], PROMOTE_DOC["env"]
    assert gate["COSIGN_VERSION"] == release["COSIGN_VERSION"]
    # The two runners differ (release is arm64, this one is x64), so the checksums must too.
    assert gate["COSIGN_SHA256"] != release["COSIGN_SHA256"]


def test_the_two_records_the_gate_checks_are_the_two_release_stores() -> None:
    release_text = RELEASE.read_text(encoding="utf-8")
    for _label, kind in promote.RECORD_KINDS:
        assert kind in release_text, kind


def test_no_signer_pattern_anywhere_in_the_gate() -> None:
    assert "regexp" not in PROMOTE_TEXT.lower()
    source = (ROOT / "scripts" / "release" / "promote.py").read_text(encoding="utf-8")
    assert "regexp" not in source.lower()


def test_the_workflow_does_not_skip_the_check() -> None:
    verify = steps_of(PROMOTE_DOC)[index(steps_of(PROMOTE_DOC), is_verify)]
    assert "promote.py verify" in verify["run"]
    assert "continue-on-error" not in verify
    assert "continue-on-error" not in str(PROMOTE_DOC["jobs"]["gate"])


@pytest.mark.parametrize("job", ["gate", "propose"])
def test_every_action_in_promote_stays_pinned_to_a_commit(job: str) -> None:
    for s in steps_of(PROMOTE_DOC, job):
        if "uses" in s:
            assert len(str(s["uses"]).split("@")[1]) >= 40, s["uses"]
