"""The migration Job's hook depends on the situation (KAV-104, ADR-0070, Lab 70).

Found live in Lab 66: with the chart's own Postgres the migration was a post-install hook. Helm runs
a post-install hook only after every Deployment is ready, and the agent, executor and collector
cannot be ready before the tables exist, so a first install waited for itself forever.

The rule now, rendered with the real chart (`helm template`; without helm these skip, CI's helm job
runs them):
- an environment with its own database server (staging, prod): a pre-install hook, as before, so
  the tables are current before any service starts;
- the chart's own Postgres, FIRST install: no hook at all, an ordinary Job that starts with the
  Deployments;
- the chart's own Postgres, any upgrade: a hook, as before.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "deploy" / "charts" / "kaval"
needs_helm = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")


def _migrate_job(env: str, *, upgrade: bool) -> dict[str, Any]:
    values = ROOT / "deploy" / "environments" / env / "values.yaml"
    cmd = ["helm", "template", f"kaval-{env}", str(CHART), "-f", str(values)]
    if upgrade:
        cmd.append("--is-upgrade")
    out = subprocess.run(
        cmd, check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout
    jobs = [d for d in yaml.safe_load_all(out)
            if d and d["kind"] == "Job" and d["metadata"]["name"].endswith("-migrate-1")]
    assert len(jobs) == 1, f"expected exactly one migrate Job for {env}, got {len(jobs)}"
    return jobs[0]


def _hook(job: dict[str, Any]) -> str | None:
    return (job["metadata"].get("annotations") or {}).get("helm.sh/hook")


@needs_helm
@pytest.mark.parametrize("env", ["staging", "prod"])
def test_an_environment_with_its_own_database_migrates_before_the_install(env: str) -> None:
    assert _hook(_migrate_job(env, upgrade=False)) == "pre-install,pre-upgrade"


@needs_helm
@pytest.mark.parametrize("env", ["lab-eks", "local"])
def test_the_charts_own_postgres_first_install_has_no_hook_so_it_cannot_deadlock(env: str) -> None:
    job = _migrate_job(env, upgrade=False)
    assert _hook(job) is None
    assert job["metadata"].get("annotations") is None  # no leftover hook weight or delete policy
    # it still waits for the database before it migrates
    pod = job["spec"]["template"]["spec"]
    assert [c["name"] for c in pod["initContainers"]] == ["wait-for-postgres"]
    assert pod["containers"][0]["command"] == ["alembic", "upgrade", "head"]


@needs_helm
@pytest.mark.parametrize("env", ["lab-eks", "local"])
def test_the_charts_own_postgres_upgrade_is_still_a_hook(env: str) -> None:
    # the negative case: the new no-hook path must be first-install only
    assert _hook(_migrate_job(env, upgrade=True)) == "post-install,pre-upgrade"
