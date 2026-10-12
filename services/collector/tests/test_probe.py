"""`kaval_collector.probe`: the slowness check (KAV-81, ADR-0047). The decision logic is tested with
a fake fetcher; the real timing is tested against a throwaway local web server, so a
"slow" answer is genuinely slow and a refused connection is genuinely refused."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from kaval_collector import probe
from kaval_collector.probe import Answer, Slowness, Target
from kaval_shared.models import Signal
from sqlalchemy import select
from sqlalchemy.orm import Session

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
T = Target("kaval-demo-staging/chaos-slowpoke", "http://slowpoke:8080/")


class _Scripted:
    """Answers with the next scripted result each time it is asked."""

    def __init__(self, *answers: Answer) -> None:
        self._answers = list(answers)
        self.asked = 0

    def __call__(self, url: str, timeout: float) -> Answer:
        self.asked += 1
        return self._answers[min(self.asked - 1, len(self._answers) - 1)]


FAST = Answer(0.05, "answered")
SLOW = Answer(3.2, "answered")
TIMED_OUT = Answer(5.0, "timeout")
REFUSED = Answer(0.01, "unreachable")


def _passes(session: Session, fetcher: _Scripted, count: int, *, state: Slowness | None = None,
            start: datetime = NOW, every: int = 30) -> list[str]:
    state = state or Slowness()
    lines: list[str] = []
    for i in range(count):
        lines += probe.poll(session, [T], state, fetcher=fetcher,
                            now=start + timedelta(seconds=every * i))
    return lines


def _signals(session: Session) -> list[Signal]:
    return list(session.scalars(select(Signal).where(Signal.kind == probe.KIND)))


# ── the decision ──────────────────────────────────────────────────────────────────────────

def test_a_fast_service_stays_silent(db_session: Session) -> None:
    _passes(db_session, _Scripted(FAST), 10)
    assert _signals(db_session) == []


def test_one_or_two_slow_answers_are_not_enough(db_session: Session) -> None:
    _passes(db_session, _Scripted(SLOW, SLOW, FAST), 3)
    assert _signals(db_session) == []


def test_three_slow_passes_in_a_row_write_one_signal(db_session: Session) -> None:
    lines = _passes(db_session, _Scripted(SLOW), 3)
    [signal] = _signals(db_session)
    assert signal.target == T.name and signal.source == "prometheus"
    assert signal.value["seconds"] == 3.2 and signal.value["passes_in_a_row"] == 3
    assert len(lines) == 1


def test_a_fast_answer_in_the_middle_starts_the_count_again(db_session: Session) -> None:
    _passes(db_session, _Scripted(SLOW, SLOW, FAST, SLOW, SLOW), 5)
    assert _signals(db_session) == []


def test_no_answer_at_all_counts_as_slow_not_as_down(db_session: Session) -> None:
    _passes(db_session, _Scripted(TIMED_OUT), 3)
    [signal] = _signals(db_session)
    assert signal.kind == "service_slow" and signal.value["outcome"] == "timeout"


def test_a_refused_connection_is_down_not_slow(db_session: Session) -> None:
    _passes(db_session, _Scripted(REFUSED), 10)
    assert _signals(db_session) == []


def test_a_refused_connection_in_the_middle_resets_the_count(db_session: Session) -> None:
    _passes(db_session, _Scripted(SLOW, SLOW, REFUSED, SLOW, SLOW), 5)
    assert _signals(db_session) == []


def test_a_slow_service_is_renewed_but_not_flooded(db_session: Session) -> None:
    """20 passes of 30 s = 10 minutes of slowness: one signal at the third pass, then one more
    after the 300 s renewal, never one per pass."""
    _passes(db_session, _Scripted(SLOW), 20)
    assert len(_signals(db_session)) == 2


def test_a_fast_error_page_is_not_slow(db_session: Session) -> None:
    # fetch() reports any answer as "answered"; a fast 503 must therefore stay silent here.
    _passes(db_session, _Scripted(Answer(0.02, "answered")), 10)
    assert _signals(db_session) == []


def test_each_target_is_counted_on_its_own(db_session: Session) -> None:
    other = Target("kaval-demo-staging/other", "http://other:8080/")
    state = Slowness()
    for i in range(3):
        probe.poll(db_session, [T, other], state,
                   fetcher=lambda url, timeout: SLOW if "slowpoke" in url else FAST,
                   now=NOW + timedelta(seconds=30 * i))
    assert [s.target for s in _signals(db_session)] == [T.name]


# ── the settings ──────────────────────────────────────────────────────────────────────────

def test_targets_are_read_from_a_setting() -> None:
    got = probe.parse_targets("ns/a=http://a:80/x, ns/b=https://b/")
    assert got == [Target("ns/a", "http://a:80/x"), Target("ns/b", "https://b/")]
    assert probe.parse_targets("") == []


@pytest.mark.parametrize("spec", [
    "no-namespace=http://a/", "ns/a=file:///etc/passwd", "ns/a=ftp://a/", "ns/a",
    "NS/a=http://a/", "ns/a b=http://a/",
])
def test_a_bad_setting_stops_the_start_up(spec: str) -> None:
    with pytest.raises(ValueError):
        probe.parse_targets(spec)


def test_too_many_targets_are_refused() -> None:
    spec = ",".join(f"ns/t{i}=http://t{i}/" for i in range(probe.MAX_TARGETS + 1))
    with pytest.raises(ValueError):
        probe.parse_targets(spec)


# ── the real clock, against a real (local) server ────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/slow":
            time.sleep(1.5)
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/elsewhere")
            self.end_headers()
            return
        self.send_response(503 if self.path == "/error" else 200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture()
def server() -> Iterator[str]:
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def test_fetch_times_a_fast_answer(server: str) -> None:
    answer = probe.fetch(f"{server}/", timeout=3)
    assert answer.outcome == "answered" and answer.seconds < 0.5


def test_fetch_times_a_genuinely_slow_answer(server: str) -> None:
    answer = probe.fetch(f"{server}/slow", timeout=5)
    assert answer.outcome == "answered" and answer.seconds >= 1.4
    assert probe.is_slow(answer, probe.SLOW_SECONDS)


def test_fetch_reports_no_answer_in_time_as_a_timeout(server: str) -> None:
    answer = probe.fetch(f"{server}/slow", timeout=0.3)
    assert answer.outcome == "timeout" and probe.is_slow(answer, probe.SLOW_SECONDS)


def test_fetch_reports_a_fast_error_page_as_answered_and_not_slow(server: str) -> None:
    answer = probe.fetch(f"{server}/error", timeout=3)
    assert answer.outcome == "answered" and not probe.is_slow(answer, probe.SLOW_SECONDS)


def test_fetch_reports_a_refused_connection_as_unreachable_and_not_slow() -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    # Windows takes about two seconds to report a refused loopback connection (Linux is instant),
    # so the limit is generous: this test is about what is reported, not how fast.
    answer = probe.fetch(f"http://127.0.0.1:{port}/", timeout=8)
    assert answer.outcome == "unreachable" and not probe.is_slow(answer, probe.SLOW_SECONDS)


def test_fetch_does_not_follow_a_redirect(server: str) -> None:
    # The redirect points at a closed port; following it would report "unreachable".
    answer = probe.fetch(f"{server}/redirect", timeout=3)
    assert answer.outcome == "answered"
