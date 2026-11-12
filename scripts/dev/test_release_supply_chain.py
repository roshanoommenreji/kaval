"""Every image the release builds gets a parts list, a signed build record and a signature.

KAV-137 added the parts list and the signed build record, KAV-138 the signature kept in the
registry beside the image.

The release workflow can only be run for real on `main` (AWS accepts nothing else), so these tests
read the workflow file and keep the safety properties in place: the parts list is made from the
image that was scanned and before any AWS access, the build record is signed by this run and must
be in the store before the release finishes, the public record never carries the AWS account
number, and no job has more power than it needs. The signing step is also run for real against a
stand-in signing tool, so "already signed", "needs signing" and "cannot be verified" are each shown
to do what they should.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
RELEASE = ROOT / ".github" / "workflows" / "release.yml"

SERVICES = ["gateway", "collector", "agent", "executor", "backup"]


def _doc() -> dict[str, Any]:
    return yaml.safe_load(RELEASE.read_text(encoding="utf-8"))


def _steps() -> list[dict[str, Any]]:
    return _doc()["jobs"]["publish"]["steps"]


def _index(predicate: Callable[[dict[str, Any]], bool]) -> int:
    for i, step in enumerate(_steps()):
        if predicate(step):
            return i
    raise AssertionError("step not found")


def _is_sbom(step: dict[str, Any]) -> bool:
    return "cyclonedx" in str(step.get("run", ""))


def _is_upload_sbom(step: dict[str, Any]) -> bool:
    uses = str(step.get("uses", ""))
    return uses.startswith("actions/upload-artifact") and "sbom" in str(
        step.get("with", {}).get("name", "")
    )


def _is_attest(step: dict[str, Any]) -> bool:
    return str(step.get("uses", "")).startswith("actions/attest-build-provenance@")


def _is_sign(step: dict[str, Any]) -> bool:
    return "./cosign sign" in str(step.get("run", ""))


def _is_install_cosign(step: dict[str, Any]) -> bool:
    return "cosign-linux-arm64" in str(step.get("run", ""))


def _is_sbom_attest(step: dict[str, Any]) -> bool:
    return str(step.get("uses", "")).startswith("actions/attest@")


def _is_verify(step: dict[str, Any]) -> bool:
    return "/attestations/" in str(step.get("run", ""))


def _is_aws_login(step: dict[str, Any]) -> bool:
    return str(step.get("uses", "")).startswith("aws-actions/configure-aws-credentials@")


def _is_push(step: dict[str, Any]) -> bool:
    return step.get("id") == "push"


def test_the_five_services_are_all_built_by_the_one_matrix() -> None:
    assert _doc()["jobs"]["publish"]["strategy"]["matrix"]["service"] == SERVICES


def test_the_parts_list_is_made_from_the_scanned_image_before_aws_is_touched() -> None:
    step = _steps()[_index(_is_sbom)]
    # The local image the scan read and the push uploads: one object, so the list is of what ships.
    assert "kaval/${{ matrix.service }}:release" in step["run"]
    assert "--format cyclonedx" in step["run"]
    # No AWS access has happened yet, so a failure here publishes nothing.
    assert _index(_is_sbom) < _index(_is_aws_login) < _index(_is_push)


def test_the_parts_list_is_checked_not_just_written() -> None:
    run = _steps()[_index(_is_sbom)]["run"]
    assert 'bom["bomFormat"] == "CycloneDX"' in run
    assert "suspiciously short" in run  # an empty list would be worse than none


def test_the_parts_list_is_kept_for_ninety_days_and_a_missing_one_fails() -> None:
    upload = _steps()[_index(_is_upload_sbom)]
    assert upload["with"]["name"] == "sbom-${{ matrix.service }}"
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["retention-days"] == 90


def test_the_build_record_is_signed_for_the_digest_ecr_reported() -> None:
    push = _steps()[_index(_is_push)]
    assert 'echo "digest=$digest" >> "$GITHUB_OUTPUT"' in push["run"]
    attest = _steps()[_index(_is_attest)]
    assert attest["with"]["subject-digest"] == "${{ steps.push.outputs.digest }}"
    assert _index(_is_push) < _index(_is_attest)


def test_the_attestation_action_is_pinned_to_a_full_commit() -> None:
    uses = _steps()[_index(_is_attest)]["uses"]
    assert re.fullmatch(r"actions/attest-build-provenance@[0-9a-f]{40}", uses), uses


def test_the_public_record_carries_no_registry_host_or_account_number() -> None:
    name = _steps()[_index(_is_attest)]["with"]["subject-name"]
    assert name == "kaval/${{ matrix.service }}"
    assert "amazonaws" not in name
    # A registry host always has dots and carries the account number; the expression is the
    # only place a dot may appear.
    assert "." not in name.replace("matrix.service", "")
    assert "registry" not in name


def test_the_records_must_be_in_the_store_for_the_exact_digest_or_the_release_stops() -> None:
    run = _steps()[_index(_is_verify)]["run"]
    assert _index(_is_attest) < _index(_is_verify)
    assert _index(_is_sbom_attest) < _index(_is_verify)
    # By the digest ECR reported, never a tag that could move.
    assert 'attestations/$DIGEST"' in run
    # Both kinds are required: the build record and the parts list.
    assert "https://slsa.dev/provenance/v1" in run
    assert "https://cyclonedx.org/bom" in run
    # Giving up must fail the job, not pass quietly.
    assert "exit 1" in run
    assert "-ge 1" in run


def test_a_check_that_always_passes_would_not_satisfy_the_rule_above() -> None:
    # The negative case: a step that never fails (`|| true`) has no `exit 1` and no count.
    lazy = 'gh api "repos/$GITHUB_REPOSITORY/attestations/$DIGEST" || true'
    assert "exit 1" not in lazy
    assert "-ge 1" not in lazy


def test_only_the_publish_job_can_sign_and_nothing_gets_more_than_it_needs() -> None:
    doc = _doc()
    assert doc["permissions"] == {}  # the default for every job is nothing
    publish = doc["jobs"]["publish"]["permissions"]
    assert publish == {"id-token": "write", "attestations": "write", "contents": "read"}
    propose = doc["jobs"]["propose"]["permissions"]
    # The job that writes to the repository can neither reach AWS nor sign a build record.
    assert "id-token" not in propose
    assert "attestations" not in propose


def _terraform() -> str:
    return (ROOT / "infra" / "modules" / "ci-publish" / "main.tf").read_text(encoding="utf-8")


def _statements() -> dict[str, str]:
    """Each statement of the role's permissions policy, by its sid."""
    text = _terraform()
    policy = text[text.index('data "aws_iam_policy_document" "publish"') :]
    out: dict[str, str] = {}
    for m in re.finditer(r'statement \{\s*sid\s*=\s*"(\w+)"(.*?)\n  \}', policy, re.S):
        out[m.group(1)] = m.group(2)
    return out


def test_the_role_can_read_its_own_repositories_to_sign_and_check_and_nothing_else() -> None:
    statements = _statements()
    assert set(statements) == {
        "RegistryLogin",
        "PushKavalImages",
        "ReadKavalImagesToSignAndCheckThem",
    }
    read = statements["ReadKavalImagesToSignAndCheckThem"]
    actions = set(re.findall(r'"(ecr:\w+)"', read))
    assert actions == {"ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"}
    # The five Kaval repositories, not "*".
    assert "resources = var.ecr_repository_arns" in read


def test_the_role_still_cannot_delete_or_change_anything_else() -> None:
    text = _terraform()
    for forbidden in (
        "BatchDeleteImage",
        "DeleteRepository",
        "PutLifecyclePolicy",
        "SetRepositoryPolicy",
        "PutImageTagMutability",
        "ecr:*",
    ):
        assert forbidden not in text, forbidden
    # The only statement allowed to name every resource is the registry login.
    for sid, body in _statements().items():
        if sid != "RegistryLogin":
            assert 'resources = ["*"]' not in body, sid


# ---- the signature (KAV-138) -------------------------------------------------------------


def test_cosign_is_fetched_at_a_fixed_version_and_refused_if_the_checksum_differs() -> None:
    doc = _doc()
    assert re.fullmatch(r"\d+\.\d+\.\d+", doc["env"]["COSIGN_VERSION"])
    assert re.fullmatch(r"[0-9a-f]{64}", doc["env"]["COSIGN_SHA256"])
    run = _steps()[_index(_is_install_cosign)]["run"]
    assert "sha256sum --check --strict" in run
    assert "v${COSIGN_VERSION}" in run
    assert "latest" not in run
    # Fetched and checked before AWS is touched, like the scanner.
    assert _index(_is_install_cosign) < _index(_is_aws_login)


def test_the_image_is_signed_by_digest_after_the_push_and_only_by_this_workflow_on_main() -> None:
    assert _index(_is_push) < _index(_is_sign)
    step = _steps()[_index(_is_sign)]
    assert step["env"]["DIGEST"] == "${{ steps.push.outputs.digest }}"
    run = step["run"]
    assert '@$DIGEST"' in run  # the digest, never a tag that could move
    assert "--yes" in run
    assert "--key" not in run  # keyless: there is no key to keep or to leak
    env = _doc()["env"]
    assert env["SIGNER_IDENTITY"] == (
        "https://github.com/roshanoommenreji/kaval/.github/workflows/release.yml@refs/heads/main"
    )
    assert env["SIGNER_ISSUER"] == "https://token.actions.githubusercontent.com"
    # Spelled out in full: a pattern would let another workflow or branch pass.
    assert "regexp" not in RELEASE.read_text(encoding="utf-8")


def test_the_sbom_attestation_action_is_pinned_and_named_without_a_registry_host() -> None:
    step = _steps()[_index(_is_sbom_attest)]
    assert re.fullmatch(r"actions/attest@[0-9a-f]{40}", step["uses"]), step["uses"]
    assert step["with"]["subject-name"] == "kaval/${{ matrix.service }}"
    assert step["with"]["subject-digest"] == "${{ steps.push.outputs.digest }}"
    assert step["with"]["sbom-path"] == "sbom/${{ matrix.service }}.cdx.json"
    # Kept in GitHub's store: nothing is pushed to the registry by this action.
    assert "push-to-registry" not in step["with"]
    assert _index(_is_push) < _index(_is_sbom_attest)


def _run_sign_step(tmp_path: Path, *, presigned: bool, verifiable: bool) -> tuple[int, list[str]]:
    """Run the real signing step's script with a stand-in for cosign, and report what it did."""
    bash = shutil.which("bash")
    if bash is None:
        raise AssertionError("bash is needed to run the step")
    script = _steps()[_index(_is_sign)]["run"].replace("${{ matrix.service }}", "gateway")
    log = tmp_path / "calls.log"
    fake = tmp_path / "cosign"
    fake.write_text(
        "#!/bin/bash\n"
        'echo "$@" >> "$FAKE_LOG"\n'
        'case "$1" in\n'
        '  verify) [ "$FAKE_VERIFIABLE" = 1 ] && { [ "$FAKE_PRESIGNED" = 1 ] '
        '|| [ -f "$FAKE_STATE" ]; } ;;\n'
        '  sign) touch "$FAKE_STATE" ;;\n'
        "esac\n",
        encoding="utf-8",
        newline="\n",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    env = {
        **os.environ,
        "REGISTRY": "registry.example",
        "DIGEST": "sha256:" + "a" * 64,
        "SIGNER_IDENTITY": _doc()["env"]["SIGNER_IDENTITY"],
        "SIGNER_ISSUER": _doc()["env"]["SIGNER_ISSUER"],
        "FAKE_LOG": str(log),
        "FAKE_STATE": str(tmp_path / "signed"),
        "FAKE_PRESIGNED": "1" if presigned else "0",
        "FAKE_VERIFIABLE": "1" if verifiable else "0",
    }
    done = subprocess.run(
        [bash, "-eo", "pipefail", "-c", script], cwd=tmp_path, env=env, capture_output=True
    )
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return done.returncode, calls


def test_an_image_that_is_already_signed_is_left_alone(tmp_path: Path) -> None:
    code, calls = _run_sign_step(tmp_path, presigned=True, verifiable=True)
    assert code == 0
    assert [c.split()[0] for c in calls] == ["verify"]  # a re-run never signs twice


def test_an_unsigned_image_is_signed_once_and_then_checked(tmp_path: Path) -> None:
    code, calls = _run_sign_step(tmp_path, presigned=False, verifiable=True)
    assert code == 0
    assert [c.split()[0] for c in calls] == ["verify", "sign", "verify"]
    identity = _doc()["env"]["SIGNER_IDENTITY"]
    for call in calls:
        if call.startswith("verify"):
            # Every check names who may have signed, and that is this workflow on main only.
            assert f"--certificate-identity {identity}" in call
            assert "--certificate-oidc-issuer https://token.actions.githubusercontent.com" in call
    assert "--yes" in calls[1]


def test_a_signature_that_cannot_be_verified_stops_the_release(tmp_path: Path) -> None:
    # The negative case: signed, but the check refuses it (wrong identity, unreadable, missing).
    code, calls = _run_sign_step(tmp_path, presigned=False, verifiable=False)
    assert code != 0
    assert [c.split()[0] for c in calls] == ["verify", "sign", "verify"]


def test_the_signing_checks_print_nothing_because_their_output_names_the_registry() -> None:
    run = _steps()[_index(_is_sign)]["run"]
    assert ">/dev/null" in run
