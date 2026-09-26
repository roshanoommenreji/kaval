"""/healthz: 200 only when Postgres is migrated and the configured model is pulled.

No __init__.py here on purpose: services/shared/tests is already a package named `tests`,
and a second one would collide under pytest's default import mode.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from kaval_gateway import main
from kaval_gateway.main import Check

client = TestClient(main.app)


def _stub(monkeypatch: pytest.MonkeyPatch, pg: Check, ol: Check) -> None:
    monkeypatch.setattr(main, "check_postgres", lambda: pg)
    monkeypatch.setattr(main, "check_ollama", lambda: ol)


def test_healthy_stack_returns_200(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, Check(ok=True, detail="migrated"), Check(ok=True, detail="ready"))
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_any_failed_dependency_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, Check(ok=True, detail="migrated"), Check(ok=False, detail="down"))
    resp = client.get("/healthz")
    assert resp.status_code == 503
    assert resp.json()["status"] == "degraded"


def _fake_tags(models: list[str]) -> Any:
    def fake_get(url: str, timeout: float) -> httpx.Response:
        body = {"models": [{"name": m} for m in models]}
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))

    return fake_get


def test_ollama_up_but_model_missing_is_unhealthy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_MODEL", "gemma3:1b")
    monkeypatch.setattr(httpx, "get", _fake_tags(["qwen3:1.7b"]))
    assert main.check_ollama() == Check(ok=False, detail="gemma3:1b not pulled")


def test_ollama_with_model_is_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_MODEL", "gemma3:1b")
    monkeypatch.setattr(httpx, "get", _fake_tags(["gemma3:1b"]))
    assert main.check_ollama().ok


def test_unreachable_postgres_reports_type_not_message(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> Any:
        raise ConnectionError("postgresql://kaval:secret@db/kaval refused")

    monkeypatch.setattr(main, "get_engine", boom)
    result = main.check_postgres()
    assert result == Check(ok=False, detail="ConnectionError")
    assert "secret" not in result.detail
