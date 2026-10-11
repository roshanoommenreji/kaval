"""The chaos objects in the chart (KAV-79, ADR-0044): absent from prod, narrow where present.

Renders the real chart with `helm template`, so it needs helm; without it the tests skip (CI's helm
job also greps the prod render for the word "chaos", which covers the prod half there)."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "deploy" / "charts" / "kaval"
pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")


def _render(env: str) -> list[str]:
    out = subprocess.run(
        ["helm", "template", f"kaval-{env}", str(CHART), "-f",
         str(ROOT / "deploy" / "environments" / env / "values.yaml")],
        check=True, capture_output=True, text=True, encoding="utf-8",
    ).stdout
    return [doc for doc in re.split(r"(?m)^---\s*$", out) if doc.strip()]


def _find(docs: list[str], kind: str, name_part: str) -> str:
    for doc in docs:
        if re.search(rf"(?m)^kind: {kind}$", doc) and name_part in doc.split("name:", 1)[-1][:80]:
            return doc
    raise AssertionError(f"no {kind} containing {name_part!r}")


def test_prod_renders_no_chaos_object_at_all() -> None:
    assert not [d for d in _render("prod") if "chaos" in d.lower()]


@pytest.mark.parametrize("env", ["staging", "local"])
def test_the_switch_is_rendered_off(env: str) -> None:
    cm = _find(_render(env), "ConfigMap", "chaos-switch")
    assert re.search(r'(?m)^\s+enabled: "false"$', cm)


@pytest.mark.parametrize("env", ["staging", "local"])
def test_the_cronjob_is_suspended_with_a_hard_limit(env: str) -> None:
    cj = _find(_render(env), "CronJob", "chaos-oom")
    assert re.search(r"(?m)^\s+suspend: true$", cj)
    assert re.search(r"(?m)^\s+activeDeadlineSeconds: 1500$", cj)
    assert re.search(r"(?m)^\s+backoffLimit: 0$", cj)
    assert f"value: \"{env}\"" in cj


def test_the_runner_can_only_write_in_the_demo_namespace() -> None:
    docs = _render("staging")
    role = _find(docs, "Role", "chaos-runner")
    assert "namespace: kaval-demo-staging" in role
    assert 'verbs: ["get", "create", "delete"]' in role
    # No other grant for the runner anywhere but the named switch ConfigMap.
    runner_roles = [d for d in docs if re.search(r"(?m)^kind: Role$", d) and "chaos" in d]
    assert len(runner_roles) == 2
    switch_role = _find(docs, "Role", "chaos-switch-reader")
    assert "resourceNames" in switch_role and 'verbs: ["get"]' in switch_role
    assert not [d for d in docs if re.search(r"(?m)^kind: Cluster", d) and "chaos" in d]


def test_the_executor_gains_only_deployment_get_patch_and_replicaset_get() -> None:
    role = _find(_render("staging"), "Role", "executor")
    assert 'resources: ["deployments"]\n    verbs: ["get", "patch"]' in role
    assert 'resources: ["replicasets"]\n    verbs: ["get"]' in role
    assert "\"create\"" not in role
