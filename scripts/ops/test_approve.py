"""`approve.py` sends the approver token the gateway now requires (KAV-115, ADR-0073)."""

from __future__ import annotations

import io
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import approve  # noqa: E402

TOKEN = "k" * 43


class _Reply(io.BytesIO):
    def __enter__(self) -> _Reply:
        return self

    def __exit__(self, *_: object) -> None:
        return None


def test_the_call_carries_the_token_as_a_bearer_header(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[urllib.request.Request] = []

    def fake_urlopen(req: urllib.request.Request, timeout: int) -> _Reply:
        seen.append(req)
        return _Reply(json.dumps({"verdict": "approved"}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    approve.decide("http://localhost:8000", "abc", "approved", "roshan", None, TOKEN)
    assert seen[0].get_header("Authorization") == f"Bearer {TOKEN}"
    assert seen[0].get_method() == "POST"


def test_without_a_token_in_the_environment_it_stops_before_calling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(approve.TOKEN_ENV, raising=False)
    called: list[Any] = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: called.append(a))
    with pytest.raises(SystemExit) as stopped:
        approve.main(["abc", "approved"])
    assert approve.TOKEN_ENV in str(stopped.value) and called == []


def test_the_token_is_never_a_command_line_option() -> None:
    with pytest.raises(SystemExit):
        approve.main(["abc", "approved", "--token", TOKEN])
