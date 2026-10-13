"""`kaval_collector.heartbeat`: the outside "still alive" ping (KAV-83, ADR-0052).

The decisions are tested with a fake sender and a fake clock; what the real request does (no
redirect followed, a timeout, a refused connection) is tested against a throwaway local web server.
The fake address is built from pieces so no secret scanner mistakes it for a real one."""

from __future__ import annotations

import socket
import threading
import urllib.error
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import NoReturn

import pytest
from kaval_collector import heartbeat, prometheus
from kaval_collector.heartbeat import Heartbeat
from kaval_collector.prometheus import PrometheusError, Sample
from sqlalchemy.orm import Session

PING = "https://hc-ping." + "example/" + "-".join(
    ["aaaa1111", "bbbb", "cccc", "dddd", "eeee55556666"])


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _Sender:
    """Answers with the next scripted result (None = worked) each time it is asked."""

    def __init__(self, *results: str | None) -> None:
        self.results = list(results)
        self.sent: list[str] = []

    def __call__(self, url: str) -> str | None:
        self.sent.append(url)
        return self.results[min(len(self.sent) - 1, len(self.results) - 1)]


def _beat(sender: _Sender, clock: _Clock, **kw: float) -> Heartbeat:
    return Heartbeat(PING, sender=sender, clock=clock, **kw)


# ── the address ─────────────────────────────────────────────────────────────────────────────

def test_an_empty_address_means_off() -> None:
    sender = _Sender(None)
    beat = Heartbeat("", sender=sender)
    assert not beat.enabled and beat.beat() == [] and sender.sent == []


@pytest.mark.parametrize("bad", [
    "http://hc-ping.example/abc", "ftp://hc-ping.example/abc", "hc-ping.example/abc", "https://",
    "https://user:pw@hc-ping.example/abc",
])
def test_anything_but_a_plain_https_address_is_refused_loudly(bad: str) -> None:
    with pytest.raises(ValueError) as caught:
        Heartbeat(bad)
    assert "abc" not in str(caught.value) and "pw" not in str(caught.value)  # never the address


def test_a_good_address_is_accepted_and_trimmed() -> None:
    assert heartbeat.check_url(f"  {PING}\n") == PING


# ── when it pings ───────────────────────────────────────────────────────────────────────────

def test_the_first_good_pass_pings_and_says_so_once() -> None:
    sender, clock = _Sender(None), _Clock()
    beat = _beat(sender, clock)
    assert beat.beat() == ["heartbeat: first ping sent"]
    assert sender.sent == [PING]


def test_it_pings_at_most_once_per_interval() -> None:
    sender, clock = _Sender(None), _Clock()
    beat = _beat(sender, clock, every=120.0)
    beat.beat()
    clock.now += 30
    assert beat.beat() == [] and len(sender.sent) == 1  # 30 s later: not due
    clock.now += 100  # 130 s since the ping
    assert beat.beat() == [] and len(sender.sent) == 2  # due, and nothing new to say


def test_a_failed_ping_is_said_once_retried_sooner_and_recovery_is_said_once() -> None:
    sender, clock = _Sender(None, "timed out", "timed out", None), _Clock()
    beat = _beat(sender, clock, every=120.0, retry=60.0)
    beat.beat()
    clock.now += 120
    failing = beat.beat()
    assert len(failing) == 1 and "failing (timed out)" in failing[0]
    clock.now += 30
    assert beat.beat() == [] and len(sender.sent) == 2  # a retry is not due for 60 s
    clock.now += 40
    assert beat.beat() == [] and len(sender.sent) == 3  # retried; still failing; not repeated
    clock.now += 60
    assert beat.beat() == ["heartbeat: pings are working again"]


def test_nothing_it_prints_contains_the_address() -> None:
    sender, clock = _Sender("could not connect", None), _Clock()
    beat = _beat(sender, clock, every=1.0, retry=1.0)
    lines = beat.beat()  # failing
    clock.now += 5
    lines += beat.beat()  # working again
    assert len(lines) == 2 and all(PING not in line and "example" not in line for line in lines)


# ── the real request ────────────────────────────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    hits: list[str] = []

    def do_GET(self) -> None:  # noqa: N802 - the name http.server calls
        _Handler.hits.append(self.path)
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/elsewhere")
            self.end_headers()
            return
        if self.path == "/slow":
            threading.Event().wait(1.5)
        self.send_response(404 if self.path == "/missing" else 200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture()
def server() -> Iterator[str]:
    _Handler.hits = []
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def test_a_working_ping_returns_none(server: str) -> None:
    assert heartbeat.send(f"{server}/ping", timeout=3) is None
    assert _Handler.hits == ["/ping"]


def test_an_error_answer_is_a_failure_with_its_code_only(server: str) -> None:
    assert heartbeat.send(f"{server}/missing", timeout=3) == "HTTP 404"


def test_a_redirect_is_not_followed(server: str) -> None:
    assert heartbeat.send(f"{server}/redirect", timeout=3) == "HTTP 302"
    assert "/elsewhere" not in _Handler.hits


def test_no_answer_in_time_is_a_timeout(server: str) -> None:
    assert heartbeat.send(f"{server}/slow", timeout=0.3) == "timed out"


def test_a_refused_connection_is_a_failure_not_an_exception() -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    # Windows takes about two seconds to report a refused loopback connection; be generous.
    assert heartbeat.send(f"http://127.0.0.1:{port}/", timeout=8) == "could not connect"


def test_an_unexpected_error_is_still_only_a_reason() -> None:
    class _Broken:
        def open(self, req: object, timeout: float) -> NoReturn:
            raise urllib.error.URLError(f"cannot reach {PING}")

    reason = heartbeat.send(PING, opener=_Broken())
    assert reason == "could not connect" and PING not in reason

    class _Odd:
        def open(self, req: object, timeout: float) -> NoReturn:
            raise RuntimeError(PING)

    reason = heartbeat.send(PING, opener=_Odd())
    assert reason == "failed (RuntimeError)" and PING not in reason


# ── in the poller's loop ────────────────────────────────────────────────────────────────────

def test_the_poller_pings_only_after_a_pass_that_fully_worked(
    db_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drives the real `main` loop: a pass where Prometheus does not answer sends nothing."""
    answers = [True, False, True]  # per pass; True = Prometheus answers
    state = {"pass": -1}
    beats: list[int] = []

    class _Flaky:
        def __init__(self, _url: str) -> None: ...

        def query(self, promql: str) -> list[Sample]:
            if "container_memory_working_set_bytes" in promql:
                state["pass"] += 1
            if not answers[state["pass"]]:
                raise PrometheusError("down")
            watchdog = [Sample({"alertname": prometheus.WATCHDOG}, 1)]
            return watchdog if prometheus.WATCHDOG in promql else []

    class _Recorder:
        def __init__(self, url: str) -> None:
            assert url == PING  # the setting reaches it, nothing else does

        def beat(self) -> list[str]:
            beats.append(state["pass"])
            return []

    def sleep(_seconds: float) -> None:
        if state["pass"] >= len(answers) - 1:
            raise KeyboardInterrupt

    monkeypatch.setattr(prometheus, "HttpPrometheus", _Flaky)
    monkeypatch.setattr(heartbeat, "Heartbeat", _Recorder)
    monkeypatch.setattr("time.sleep", sleep)
    monkeypatch.setenv("KAVAL_METRICS_PORT", "0")

    prometheus.main(["--every", "1", "--heartbeat-url", PING])

    assert beats == [0, 2]  # not pass 1, when Prometheus did not answer


def test_the_poller_refuses_to_start_on_an_insecure_address() -> None:
    with pytest.raises(SystemExit) as stopped:
        prometheus.main(["--heartbeat-url", "http://hc-ping.example/abc"])
    assert "https" in str(stopped.value) and "hc-ping.example" not in str(stopped.value)
