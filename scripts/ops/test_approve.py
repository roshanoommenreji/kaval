"""`approve.py` sends the approver token the gateway requires (KAV-115, ADR-0073) and never takes
it to a different address (KAV-124, ADR-0075).

Left alone, Python's `urllib` follows a redirect and copies the request's headers to the new
address, `Authorization` included. The tests that matter use two real web servers on this
computer: one plays the gateway and answers "go to the other one", the other records what it is
sent. Nothing is mocked there.
"""

from __future__ import annotations

import contextlib
import http.server
import json
import sys
import threading
import time
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import approve  # noqa: E402

TOKEN = "k" * 43
REDIRECT_STATUSES = [301, 302, 303, 307, 308]  # the answers that say "go there instead"
FOLLOWED_FOR_A_POST = [301, 302, 303]  # the ones urllib's own handler follows for a POST

Answer = Callable[[str], tuple[int, dict[str, str], str]]  # path -> status, headers, body


class Site:
    """A local web server. It records the method, path and Authorization header of every request
    and answers as `answer(path)` says."""

    def __init__(self, answer: Answer) -> None:
        self.seen: list[tuple[str, str, str | None]] = []
        self.bodies: list[bytes] = []
        site = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _reply(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                site.bodies.append(self.rfile.read(length) if length else b"")
                site.seen.append((self.command, self.path, self.headers.get("Authorization")))
                status, headers, body = answer(self.path)
                payload = body.encode()
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = _reply

            def log_message(self, format: str, *args: object) -> None:
                pass  # keep the test output quiet

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


Start = Callable[[Answer], Site]


@pytest.fixture
def site() -> Iterator[Start]:
    started: list[Site] = []

    def start(answer: Answer) -> Site:
        made = Site(answer)
        started.append(made)
        return made

    yield start
    for made in started:
        made.close()


def _decide(base: str) -> dict[str, object]:
    return approve.decide(base, "abc", "approved", "roshan", None, TOKEN)


def test_the_call_carries_the_token_as_a_bearer_header(site: Start) -> None:
    gateway = site(lambda path: (200, {}, json.dumps({"verdict": "approved"})))
    assert _decide(gateway.base) == {"verdict": "approved"}
    assert gateway.seen == [("POST", "/v1/actions/abc/decisions", f"Bearer {TOKEN}")]
    assert json.loads(gateway.bodies[0]) == {"verdict": "approved", "actor": "roshan"}


@pytest.mark.parametrize("status", REDIRECT_STATUSES)
def test_a_redirected_call_never_takes_the_token_to_another_address(
    site: Start, status: int
) -> None:
    elsewhere = site(lambda path: (200, {}, "{}"))
    gateway = site(lambda path: (status, {"Location": f"{elsewhere.base}{path}"}, ""))
    with contextlib.suppress(BaseException):  # how the call ends is the next test's business
        _decide(gateway.base)
    time.sleep(0.2)  # a moment for a request that must not come
    assert elsewhere.seen == []  # not the token, and not even a request
    assert len(gateway.seen) == 1


def test_a_redirected_call_stops_with_a_plain_message(site: Start) -> None:
    marker = "-".join(["signed", "link", "marker"])  # stands for a token in a redirect's address
    elsewhere = site(lambda path: (200, {}, "{}"))
    gateway = site(lambda path: (302, {"Location": f"{elsewhere.base}/{marker}"}, ""))
    with pytest.raises(SystemExit) as stopped:
        _decide(gateway.base)
    message = str(stopped.value)
    assert message.startswith(
        "the gateway answered with a redirect (HTTP 302). It was not followed, so the approver "
        "token stays on this address."
    )
    assert "look before running it again" in message and "KAVAL_GATEWAY_URL" in message
    for hidden in (gateway.base, elsewhere.base, marker, TOKEN):
        assert hidden not in message
    assert stopped.value.__cause__ is None and stopped.value.__suppress_context__
    assert len(gateway.seen) == 1


def test_a_redirect_to_the_same_address_is_refused_too(site: Start) -> None:
    gateway = site(lambda path: (302, {"Location": "/somewhere/else"}, ""))
    with pytest.raises(SystemExit, match="answered with a redirect"):
        _decide(gateway.base)
    assert len(gateway.seen) == 1  # it did not go to /somewhere/else


def test_other_gateway_errors_are_reported_as_before(site: Start) -> None:
    gateway = site(lambda path: (401, {}, "login needed"))
    with pytest.raises(SystemExit) as stopped:
        _decide(gateway.base)
    assert str(stopped.value) == "401 Unauthorized: login needed"


def test_a_gateway_that_does_not_answer_is_reported_as_before(site: Start) -> None:
    closed = site(lambda path: (200, {}, "{}"))
    closed.close()  # nothing is listening on that port any more
    with pytest.raises(SystemExit) as stopped:
        _decide(closed.base)
    assert "could not reach the gateway" in str(stopped.value)
    assert "make dev-tunnel" in str(stopped.value)


def test_without_a_token_in_the_environment_it_stops_before_calling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(approve.TOKEN_ENV, raising=False)
    called: list[Any] = []
    # Any way of opening a connection, whichever opener the script uses.
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", lambda *a, **k: called.append(a))
    with pytest.raises(SystemExit) as stopped:
        approve.main(["abc", "approved"])
    assert approve.TOKEN_ENV in str(stopped.value) and called == []


def test_the_token_is_never_a_command_line_option() -> None:
    with pytest.raises(SystemExit):
        approve.main(["abc", "approved", "--token", TOKEN])
