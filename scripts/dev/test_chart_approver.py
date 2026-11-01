"""The approver token reaches the gateway from a Secret and from nowhere else (KAV-115, ADR-0073).

Renders the real chart with `helm template`; skipped without helm (CI's helm job renders it too).
The live proof is staging: a call without the token is refused, one with it works."""

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


def _gateway_env(env: str) -> list[dict[str, Any]]:
    out = subprocess.run(
        ["helm", "template", f"kaval-{env}", str(CHART), "-f",
         str(ROOT / "deploy" / "environments" / env / "values.yaml")],
        check=True, capture_output=True, text=True, encoding="utf-8",
    ).stdout
    for doc in yaml.safe_load_all(out):
        if doc and doc["kind"] == "Deployment" and doc["metadata"]["name"].endswith("-gateway"):
            env_list: list[dict[str, Any]] = doc["spec"]["template"]["spec"]["containers"][0]["env"]
            return env_list
    raise AssertionError("no gateway Deployment rendered")


@needs_helm
@pytest.mark.parametrize("env", ["staging", "prod"])
def test_staging_and_prod_read_the_token_from_the_approver_secret(env: str) -> None:
    (token,) = [e for e in _gateway_env(env) if e["name"] == "APPROVER_TOKEN"]
    assert "value" not in token, "the token must never be a literal in the chart"
    ref = token["valueFrom"]["secretKeyRef"]
    assert ref == {"name": "kaval-approver", "key": "APPROVER_TOKEN", "optional": True}


@needs_helm
def test_an_environment_with_no_secret_name_gets_no_variable_so_the_route_stays_shut() -> None:
    # `local` sets no approverSecretName: the gateway then has no token and answers 503.
    assert [e for e in _gateway_env("local") if e["name"] == "APPROVER_TOKEN"] == []


def test_the_default_is_empty_so_nothing_is_open_by_accident() -> None:
    values = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
    assert values["gateway"]["approverSecretName"] == ""
