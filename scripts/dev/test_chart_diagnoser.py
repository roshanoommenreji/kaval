"""The model and the diagnosis worker in the chart (KAV-87, ADR-0055): absent where not switched on,
narrow where present.

Renders the real chart with `helm template`, so it needs helm; without it the tests skip."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "deploy" / "charts" / "kaval"
pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")


def _helm(env: str, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["helm", "template", f"kaval-{env}", str(CHART), "-f",
         str(ROOT / "deploy" / "environments" / env / "values.yaml"), *extra],
        capture_output=True, text=True, encoding="utf-8",
    )


def _render(env: str, *extra: str) -> list[str]:
    done = _helm(env, *extra)
    assert done.returncode == 0, done.stderr
    return [doc for doc in re.split(r"(?m)^---\s*$", done.stdout) if doc.strip()]


def _find(docs: list[str], kind: str, name: str) -> str:
    for doc in docs:
        if re.search(rf"(?m)^kind: {kind}$", doc) and re.search(rf"(?m)^  name: {name}$", doc):
            return doc
    raise AssertionError(f"no {kind} named {name}")


def test_where_it_is_not_switched_on_nothing_of_it_is_rendered() -> None:
    text = "\n".join(_render("local"))
    assert "-ollama" not in text and "-diagnoser" not in text
    assert "kaval-diagnoser.rules" not in text


@pytest.mark.parametrize("env", ["staging", "prod"])
def test_staging_and_prod_run_the_model_and_the_worker(env: str) -> None:
    docs = _render(env)
    _find(docs, "Deployment", f"kaval-{env}-ollama")
    _find(docs, "Deployment", f"kaval-{env}-diagnoser")
    _find(docs, "Service", f"kaval-{env}-ollama")


@pytest.mark.parametrize("env", ["staging", "prod"])
def test_neither_gets_a_token_to_talk_to_the_cluster(env: str) -> None:
    docs = _render(env)
    for name in (f"kaval-{env}-ollama", f"kaval-{env}-diagnoser"):
        deployment = _find(docs, "Deployment", name)
        assert "automountServiceAccountToken: false" in deployment
        assert "serviceAccountName" not in deployment


@pytest.mark.parametrize("env", ["staging", "prod"])
def test_the_model_cannot_outgrow_its_share_of_the_node(env: str) -> None:
    ollama = _find(_render(env), "Deployment", f"kaval-{env}-ollama")
    assert re.search(r"(?m)^\s+memory: 1536Mi$", ollama)
    assert "type: Recreate" in ollama  # two copies would be two models in memory
    assert 'OLLAMA_KEEP_ALIVE, value: "5m"' in ollama  # unloaded when idle
    assert "readOnlyRootFilesystem: true" in ollama
    assert "runAsNonRoot: true" in ollama
    assert 'drop: ["ALL"]' in ollama


def test_the_image_and_the_model_are_pinned() -> None:
    ollama = _find(_render("staging"), "Deployment", "kaval-staging-ollama")
    assert re.search(r"image: \"ollama/ollama:[\d.]+@sha256:[0-9a-f]{64}\"", ollama)
    # the init step compares the downloaded model's id with these and refuses a different one
    assert "REFUSING" in ollama
    assert re.search(r'CHAT_ID, value: "[0-9a-f]{12}"', ollama)
    assert re.search(r'EMBED_ID, value: "[0-9a-f]{12}"', ollama)


def test_the_model_is_only_reachable_inside_the_cluster() -> None:
    service = _find(_render("staging"), "Service", "kaval-staging-ollama")
    assert "type:" not in service  # a ClusterIP, the default; no NodePort or LoadBalancer
    assert "nodePort" not in service


def test_the_worker_asks_the_model_through_its_service_and_has_the_speed_limit() -> None:
    worker = _find(_render("staging"), "Deployment", "kaval-staging-diagnoser")
    assert "http://kaval-staging-ollama:11434" in worker
    assert 'kaval_agent.diagnoser' in worker
    for setting in ("DIAGNOSE_MAX_PER_HOUR", "DIAGNOSE_MAX_AGE_HOURS", "DIAGNOSE_TIMEOUT_SECONDS"):
        assert setting in worker
    assert 'drop: ["ALL"]' in worker


def test_the_worker_uses_the_agents_database_role_and_nothing_stronger() -> None:
    worker = _find(_render("staging"), "Deployment", "kaval-staging-diagnoser")
    assert "kaval-postgres-agent" in worker
    for stronger in ("kaval-postgres-admin", "kaval-postgres-executor", "kaval-postgres-gateway"):
        assert stronger not in worker


def test_the_worker_is_watched() -> None:
    docs = _render("staging")
    text = "\n".join(docs)
    assert "job_name: kaval-diagnoser" in text
    _find(docs, "Service", "kaval-staging-diagnoser-metrics")
    assert "kaval-diagnoser.rules.yaml" in text
    assert "KavalDiagnosisBacklog" in text and "KavalDiagnoserMissing" in text


def test_a_worker_without_a_model_is_refused_at_render_time() -> None:
    done = _helm("staging", "--set", "ollama.enabled=false")
    assert done.returncode != 0
    assert "needs ollama.enabled" in done.stderr


def test_switching_the_worker_off_removes_its_alerts_too() -> None:
    """An `absent()` rule for a worker that is not meant to exist would page forever."""
    text = "\n".join(_render("staging", "--set", "diagnoser.enabled=false"))
    assert "-diagnoser" not in text
    assert "KavalDiagnoserMissing" not in text
