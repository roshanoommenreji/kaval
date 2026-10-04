"""Pure unit tests for database_url() -- string construction only, no real connection."""

from __future__ import annotations

import pytest
from kaval_shared.db import database_url


@pytest.fixture
def postgres_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_USER", "kaval")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    monkeypatch.setenv("POSTGRES_HOST", "db.example")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("POSTGRES_DB", "kaval")


def test_sslmode_defaults_to_prefer(postgres_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POSTGRES_SSLMODE", raising=False)
    assert database_url() == "postgresql+psycopg://kaval:secret@db.example:5432/kaval?sslmode=prefer"


def test_sslmode_honours_explicit_require(
    postgres_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("POSTGRES_SSLMODE", "require")
    assert database_url() == "postgresql+psycopg://kaval:secret@db.example:5432/kaval?sslmode=require"
