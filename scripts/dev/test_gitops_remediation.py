"""Every Flux release retries a failed install and upgrade, a finite number of times (KAV-108).

Staging's first install failed on 2026-10-31 and Flux stayed stuck on `MissingRollbackTarget` even
after the cause was fixed in git. The retries were already set (3): they had run out, and a release
whose retries have run out is not retried again by itself (the same thing happened to prod on
2026-10-02, Lab 20). This test guards what is there so a tidy-up cannot drop it silently, and keeps
the number finite: a negative number means retry for ever, which hides a real fault in a loop."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
RETRIES = 3


def _release(env: str) -> dict[str, Any]:
    path = ROOT / "deploy" / "gitops" / env / "helmrelease.yaml"
    return dict(yaml.safe_load(path.read_text(encoding="utf-8"))["spec"])


def install_retries(spec: dict[str, Any]) -> int | None:
    return ((spec.get("install") or {}).get("remediation") or {}).get("retries")


def upgrade_retries(spec: dict[str, Any]) -> int | None:
    return ((spec.get("upgrade") or {}).get("remediation") or {}).get("retries")


@pytest.mark.parametrize("env", ["staging", "prod", "lab-eks"])
def test_a_failed_first_install_is_retried(env: str) -> None:
    assert install_retries(_release(env)) == RETRIES


@pytest.mark.parametrize("env", ["staging", "prod", "lab-eks"])
def test_a_failed_upgrade_is_retried(env: str) -> None:
    assert upgrade_retries(_release(env)) == RETRIES


@pytest.mark.parametrize("env", ["staging", "prod", "lab-eks"])
def test_retries_are_finite(env: str) -> None:
    spec = _release(env)
    assert 0 < (install_retries(spec) or 0) <= 5
    assert 0 < (upgrade_retries(spec) or 0) <= 5


def test_the_check_itself_refuses_a_release_that_never_retries() -> None:
    """The negative case: a release with no retries is reported as having none."""
    bare = {"interval": "1m", "releaseName": "kaval-staging"}
    assert install_retries(bare) is None
    assert upgrade_retries(bare) is None
    with_retries = {"install": {"remediation": {"retries": 3}}}
    assert install_retries(with_retries) == 3
    assert upgrade_retries(with_retries) is None
