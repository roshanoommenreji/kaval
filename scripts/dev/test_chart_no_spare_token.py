"""No workload carries a Kubernetes token it does not use (KAV-162, finding C-1, the access review).

Renders the real chart with `helm template` for every environment and runs the permission scan on
it, so a new workload that forgets `automountServiceAccountToken: false` fails here, not in an
audit. Needs helm; without it the tests skip."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import rbac_scan
import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "deploy" / "charts" / "kaval"
ENVIRONMENTS = ["local", "staging", "prod", "lab-eks"]
pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")


def _render(env: str) -> list[dict[str, Any]]:
    done = subprocess.run(
        ["helm", "template", f"kaval-{env}", str(CHART), "-f",
         str(ROOT / "deploy" / "environments" / env / "values.yaml")],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert done.returncode == 0, done.stderr
    return [d for d in yaml.safe_load_all(done.stdout) if isinstance(d, dict)]


@pytest.mark.parametrize("env", ENVIRONMENTS)
def test_no_workload_mounts_a_token_nothing_needs(env: str) -> None:
    assert rbac_scan.review(_render(env)).spare_mounts == []


@pytest.mark.parametrize("env", ["staging", "prod"])
def test_the_gateway_and_the_migration_job_are_among_those_with_no_token(env: str) -> None:
    mounts = {name: token for name, _, token, _ in rbac_scan.review(_render(env)).workloads}
    gateway = [n for n in mounts if n.startswith("Deployment/") and n.endswith("-gateway")]
    migrate = [n for n in mounts if n.startswith("Job/") and "-migrate" in n]
    assert gateway and migrate, "the scan no longer sees the gateway or the migration job"
    assert all(mounts[name] == "no" for name in gateway + migrate)
