"""Recent changes: workload -> service mapping, and honest degradation (KAV-40)."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime

import pytest
from kaval_agent import recent_changes as rc


def test_an_unmapped_workload_is_not_a_service() -> None:
    result = rc.for_workload("checkout")  # the synthetic demo's fictional workload
    assert result == rc.RecentChanges(service=None, available=False, changes=[])


def test_a_known_service_with_no_git_available_degrades_honestly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*a: object, **k: object) -> None:
        raise FileNotFoundError("git not on PATH")

    monkeypatch.setattr(subprocess, "run", boom)
    result = rc.for_workload("gateway")
    assert result.service == "gateway"
    assert result.available is False
    assert result.changes == []


def test_not_a_git_repository_also_degrades_honestly(monkeypatch: pytest.MonkeyPatch) -> None:
    def not_a_repo(*a: object, **k: object) -> None:
        raise subprocess.CalledProcessError(128, ["git", "log"])

    monkeypatch.setattr(subprocess, "run", not_a_repo)
    result = rc.for_workload("gateway")
    assert result.available is False


def test_a_slow_git_also_degrades_honestly(monkeypatch: pytest.MonkeyPatch) -> None:
    def slow(*a: object, **k: object) -> None:
        raise subprocess.TimeoutExpired(["git", "log"], 10)

    monkeypatch.setattr(subprocess, "run", slow)
    assert rc.for_workload("gateway").available is False


def test_parses_commits_newest_first(monkeypatch: pytest.MonkeyPatch) -> None:
    out = (
        "abc123def456\x1f2026-09-27T10:00:00+05:30\x1ffix: a bug\n"
        "1a2b3c4d5e6f\x1f2026-09-20T09:00:00+05:30\x1ffeat: a feature"
    )

    def fake_run(cmd: list[str], **k: object) -> subprocess.CompletedProcess[str]:
        assert cmd[:2] == ["git", "log"]
        assert "services/gateway/" in cmd
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = rc.for_workload("gateway")
    assert result.available is True
    assert [c.summary for c in result.changes] == ["fix: a bug", "feat: a feature"]
    assert result.changes[0].sha == "abc123def4"


def test_since_window_is_passed_to_git(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run(cmd: list[str], **k: object) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    now = datetime(2026, 9, 28, tzinfo=UTC)
    rc.for_workload("gateway", now=now)
    since_arg = next(a for a in captured["cmd"] if a.startswith("--since="))
    assert "2026-09-14" in since_arg  # 14 days before `now`, the default window
