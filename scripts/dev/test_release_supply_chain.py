"""Every image the release builds gets a parts list and a signed build record (KAV-137).

The release workflow can only be run for real on `main` (AWS accepts nothing else), so these tests
read the workflow file and keep the safety properties in place: the parts list is made from the
image that was scanned and before any AWS access, the build record is signed by this run and must
be in the store before the release finishes, the public record never carries the AWS account
number, and no job has more power than it needs.
"""

from __future__ import annotations

import re
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


def test_the_record_must_be_in_the_store_for_the_exact_digest_or_the_release_stops() -> None:
    run = _steps()[_index(_is_verify)]["run"]
    assert _index(_is_attest) < _index(_is_verify)
    # By the digest ECR reported, never a tag that could move.
    assert 'attestations/$DIGEST"' in run
    # Giving up must fail the job, not pass quietly.
    assert "exit 1" in run
    assert "exit 0" in run  # and only a count of one or more may pass
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


def test_the_build_record_adds_no_aws_permission() -> None:
    # The record lives in GitHub, so the build robot's push-only role is unchanged (ADR-0089).
    text = (ROOT / "infra" / "modules" / "ci-publish" / "main.tf").read_text(encoding="utf-8")
    assert "BatchGetImage" not in text
    assert "GetDownloadUrlForLayer" not in text
