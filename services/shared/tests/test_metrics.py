"""The health numbers every service shows (KAV-78, ADR-0042): what is on the page, what is
deliberately not, and that a port that cannot open never stops the work."""

from __future__ import annotations

import re
import socket
import sys
import urllib.request

import pytest
from kaval_shared import health, metrics
from prometheus_client import REGISTRY


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _scrape(port: int) -> str:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5) as response:
        return str(response.read().decode())


def test_the_page_shows_the_loop_numbers_and_nothing_that_comes_from_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = _free_port()
    monkeypatch.setenv("KAVAL_METRICS_PORT", str(port))

    assert metrics.serve("pagetest", interval=15, version="1.2.3") is True
    metrics.pass_failed("pagetest")
    body = _scrape(port)

    assert 'kaval_loop_interval_seconds{service="pagetest"} 15.0' in body
    assert 'kaval_loop_errors_total{service="pagetest"} 1.0' in body
    assert 'kaval_build_info{service="pagetest",version="1.2.3"} 1.0' in body
    if sys.platform.startswith("linux"):  # the client reads /proc, so the images have it
        assert "process_resident_memory_bytes" in body  # the real memory of each service
    # Anything on this page is readable by every pod in the cluster (Flannel enforces no
    # NetworkPolicy), so the only labels allowed are the two that name the service.
    labels = {name for line in body.splitlines() if line.startswith("kaval_")
              for name in re.findall(r'(\w+)="', line)}
    assert labels <= {"service", "version"}


def test_a_pass_moves_the_success_time_and_a_failure_does_not() -> None:
    metrics._LAST_SUCCESS.labels("movetest").set(100.0)
    metrics.pass_failed("movetest")
    assert REGISTRY.get_sample_value(
        "kaval_loop_last_success_timestamp_seconds", {"service": "movetest"}) == 100.0
    metrics.pass_ok("movetest")
    moved = REGISTRY.get_sample_value(
        "kaval_loop_last_success_timestamp_seconds", {"service": "movetest"})
    assert moved is not None and moved > 1_000_000_000


def test_a_service_that_has_not_finished_a_pass_yet_is_young_not_stalled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KAVAL_METRICS_PORT", "0")
    metrics.serve("youngtest", interval=30)
    started = REGISTRY.get_sample_value(
        "kaval_loop_last_success_timestamp_seconds", {"service": "youngtest"})
    assert started is not None and started > 1_000_000_000


def test_a_request_driven_service_gets_no_staleness_numbers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KAVAL_METRICS_PORT", "0")
    metrics.serve("requesttest")
    assert REGISTRY.get_sample_value(
        "kaval_loop_interval_seconds", {"service": "requesttest"}) is None


def test_a_port_that_cannot_open_is_reported_and_the_service_carries_on(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    with socket.socket() as taken:
        taken.bind(("0.0.0.0", 0))
        taken.listen()
        monkeypatch.setenv("KAVAL_METRICS_PORT", str(taken.getsockname()[1]))
        assert metrics.serve("busytest", interval=15) is False
    assert "metrics: not serving" in capsys.readouterr().err


def test_port_zero_turns_the_page_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAVAL_METRICS_PORT", "0")
    assert metrics.serve("offtest", interval=15) is False


@pytest.mark.parametrize(("fingerprint", "mine"), [
    ("kaval_loop_stalled:k8s:kaval-staging/executor", True),
    ("kaval_prometheus_unreachable:k8s:kaval-staging/prometheus", True),
    ("memory_pressure:k8s:kaval-demo/checkout", False),
    ("oom_killed:k8s:kaval-demo/kaval_looking", False),   # `kaval_` later in the string is not it
    ("cost_spike:aws:ebs/vol-0", False),
])
def test_only_a_cause_that_starts_kaval_underscore_is_kavals_own(
    fingerprint: str, mine: bool,
) -> None:
    assert health.is_health_fingerprint(fingerprint) is mine
