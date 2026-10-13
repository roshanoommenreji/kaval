"""The outside "still alive" ping is wired through the chart as a Secret reference (KAV-83, ADR-0052).

Renders the real chart with `helm template`, so it needs helm; without it the tests skip (CI's helm
job renders the chart too). The address itself is a secret and must never appear in the chart."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "deploy" / "charts" / "kaval"
pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")


def _render(env: str, *extra: str) -> str:
    return subprocess.run(
        ["helm", "template", f"kaval-{env}", str(CHART), "-f",
         str(ROOT / "deploy" / "environments" / env / "values.yaml"), *extra],
        check=True, capture_output=True, text=True, encoding="utf-8",
    ).stdout


def _docs(out: str) -> list[str]:
    return [d for d in re.split(r"(?m)^---\s*$", out) if d.strip()]


def _poller(out: str) -> str:
    for doc in _docs(out):
        if re.search(r"(?m)^kind: Deployment$", doc) and re.search(
                r"(?m)^  name: \S+-collector-prometheus$", doc):
            return doc
    raise AssertionError("no poller Deployment")


@pytest.mark.parametrize("env", ["staging", "prod"])
def test_the_poller_reads_the_address_from_an_optional_secret(env: str) -> None:
    poller = _poller(_render(env))
    assert re.search(
        r"- name: HEARTBEAT_URL\n +valueFrom:\n +secretKeyRef:\n +name: \"kaval-heartbeat\"\n"
        r" +key: HEARTBEAT_URL\n +optional: true\n", poller), "HEARTBEAT_URL is not an optional secret reference"


@pytest.mark.parametrize("env", ["staging", "prod"])
def test_only_the_poller_gets_it(env: str) -> None:
    out = _render(env)
    holders = [d for d in _docs(out) if "HEARTBEAT_URL" in d]
    assert len(holders) == 1 and "collector-prometheus" in holders[0]


def test_off_by_default() -> None:
    out = _render("prod", "--set", "prometheus.heartbeat.secretName=")
    assert "HEARTBEAT_URL" not in out


def test_no_address_is_ever_in_the_chart_or_the_settings() -> None:
    # a value is only ever the Secret's NAME; an address (https://...) in any heartbeat setting is a leak
    for path in [CHART / "values.yaml", *(ROOT / "deploy").glob("environments/*/values.yaml"),
                 *(ROOT / "deploy").glob("gitops/*/helmrelease.yaml")]:
        text = path.read_text(encoding="utf-8")
        for block in re.findall(r"(?ms)^\s*heartbeat:\n((?:\s+.*\n?)*)", text):
            assert "http" not in block.split("resources:")[0], f"an address in {path.name}"
