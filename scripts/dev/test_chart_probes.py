"""Readiness checks on the background programs (KAV-83, ADR-0051).

Renders the real chart with `helm template`, so it needs helm; without it the tests skip (CI's helm
job renders the chart too). The live proof is the staging run in Lab 50: a release that dies on
start-up must leave the old copy serving."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "deploy" / "charts" / "kaval"
pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")

# name part -> in which environments it is rendered (the poller only where Prometheus is on)
WORKERS = {"agent": ["staging", "prod", "local"], "executor": ["staging", "prod", "local"],
           "collector": ["staging", "prod", "local"]}


def _render(env: str) -> list[str]:
    out = subprocess.run(
        ["helm", "template", f"kaval-{env}", str(CHART), "-f",
         str(ROOT / "deploy" / "environments" / env / "values.yaml")],
        check=True, capture_output=True, text=True, encoding="utf-8",
    ).stdout
    return [doc for doc in re.split(r"(?m)^---\s*$", out) if doc.strip()]


def _deployment(docs: list[str], suffix: str) -> str:
    for doc in docs:
        if re.search(r"(?m)^kind: Deployment$", doc) and re.search(
                rf"(?m)^  name: \S+-{suffix}$", doc):
            return doc
    raise AssertionError(f"no Deployment named *-{suffix}")


def _probe(doc: str) -> dict[str, str]:
    m = re.search(
        r"(?m)^ +readinessProbe:\n +tcpSocket:\n +port: (\S+)\n +initialDelaySeconds: (\d+)\n"
        r" +periodSeconds: (\d+)\n +failureThreshold: (\d+)\n", doc)
    assert m, "no tcp readiness check on the health page"
    return dict(zip(["port", "delay", "period", "fails"], m.groups(), strict=True))


@pytest.mark.parametrize("env", ["staging", "prod", "local"])
@pytest.mark.parametrize("worker", list(WORKERS))
def test_every_background_program_waits_to_be_ready(env: str, worker: str) -> None:
    doc = _deployment(_render(env), worker)
    probe = _probe(doc)
    assert probe["port"] == "metrics"
    # the named port must exist, or the check would never pass and nothing could roll out
    assert "- { name: metrics, containerPort: 9100 }" in doc
    # never longer than a minute of failing before it is called not ready
    assert int(probe["period"]) * int(probe["fails"]) <= 60


def test_the_poller_has_it_wherever_it_runs() -> None:
    doc = _deployment(_render("staging"), "collector-prometheus")
    assert _probe(doc)["port"] == "metrics"


@pytest.mark.parametrize("worker", list(WORKERS))
def test_no_liveness_restart_on_the_workers(worker: str) -> None:
    """Deliberate (ADR-0051): a restart because the database is slow makes a bad hour worse, and a
    stalled loop is the alert's job. If someone adds one, this says why it was left out."""
    assert "livenessProbe" not in _deployment(_render("staging"), worker)


def test_prod_gets_the_same_checks_as_staging() -> None:
    for worker in WORKERS:
        assert _probe(_deployment(_render("prod"), worker)) == _probe(
            _deployment(_render("staging"), worker))
