"""The eval run refuses to start without `opa` (KAV-112): the action-safety gate depends on it."""

from __future__ import annotations

import shutil

import pytest
from kaval_agent import policy

from evals import run


def test_the_run_refuses_to_start_without_opa(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(SystemExit) as stopped:
        run.require_opa()
    assert "action-safety gate means nothing" in str(stopped.value)


def test_the_run_goes_ahead_when_opa_is_found(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def which(name: str) -> str:
        seen.append(name)
        return "/bin/opa"

    monkeypatch.setattr(shutil, "which", which)
    run.require_opa()
    assert seen == [policy.DEFAULT_OPA_BIN]
