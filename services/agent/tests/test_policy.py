"""kaval_agent.policy: classify() against the real policy/ bundle through a real `opa` binary
(KAV-42) — no mocking of OPA itself, because the property this story cares about ("nothing is
born auto until an ADR promotes it") is exactly the kind of thing a mock would quietly hide, the
way the module docstring's Lab 12 note explains.

`opa` is required tooling from Phase 2 on (docs/labs/lab-00-toolchain.md). Same shape as
services/conftest.py's KAVAL_REQUIRE_DB: missing locally, these tests skip so a laptop that
hasn't installed it yet doesn't fail `make test`; CI sets KAVAL_REQUIRE_OPA=1, where it always
installs opa first, so a skip there would hide a real regression instead of tolerating a gap."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from kaval_agent import policy
from kaval_shared.models import BlastRadius, PolicyClass

if shutil.which(policy.DEFAULT_OPA_BIN) is None:
    _reason = "opa not on PATH - install it (docs/labs/lab-00-toolchain.md) to run these tests"
    if os.environ.get("KAVAL_REQUIRE_OPA") == "1":
        pytest.fail(_reason, pytrace=False)
    pytestmark = pytest.mark.skip(reason=_reason)


def test_ordinary_action_is_ask() -> None:
    result = policy.classify("scale_deployment", BlastRadius.deployment, True, 0.7)
    assert result == PolicyClass.ask


@pytest.mark.parametrize("action_type", [
    "delete_pvc", "Remove_PersistentVolumeClaim", "attach_iam_policy",
    "terminate_ec2_instance", "modify_billing_alert", "delete_namespace",
])
def test_denylisted_types_are_never_regardless_of_how_safe_everything_else_looks(
    action_type: str,
) -> None:
    result = policy.classify(action_type, BlastRadius.pod, True, 0.99)
    assert result == PolicyClass.never


def test_account_blast_radius_is_never_even_for_an_innocuous_type() -> None:
    result = policy.classify("restart_pod", BlastRadius.account, True, 0.99)
    assert result == PolicyClass.never


def test_nothing_is_born_auto() -> None:
    """The best possible case for auto — pod, reversible, near-certain — still lands on `ask`
    while policy/promotions.json is empty, evaluated against the real file on disk, not a
    mock. This is the live counterpart to policy_test.rego's mocked version of the same claim:
    a mocked `data.auto_promotions` would have passed even if the real file's path in
    policy.rego were wrong, which is exactly what happened once while building this (the real
    file loaded at `data.auto_promotions`, not `data.promotions.promoted` as first written) —
    see ADR-0017."""
    result = policy.classify("restart_pod", BlastRadius.pod, True, 0.999)
    assert result == PolicyClass.ask


def test_evaluating_against_an_empty_policy_dir_falls_back_to_ask(tmp_path: Path) -> None:
    # No policy.rego at all here, so `data.policy.decision` is undefined. That must fail
    # closed to `ask`, not raise and not silently return something else.
    result = policy.classify(
        "restart_pod", BlastRadius.pod, True, 0.99, policy_dir=tmp_path,
    )
    assert result == PolicyClass.ask


def test_a_missing_opa_binary_falls_back_to_ask() -> None:
    result = policy.classify(
        "restart_pod", BlastRadius.pod, True, 0.99, opa_bin="opa-does-not-exist-anywhere",
    )
    assert result == PolicyClass.ask


def test_an_unrecognised_decision_falls_back_to_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(policy, "_run_opa", lambda *a, **kw: "quarantine")
    result = policy.classify("restart_pod", BlastRadius.pod, True, 0.99)
    assert result == PolicyClass.ask
