"""A request to Atlassian has a time limit, and no answer is a plain failure (KAV-122); a redirect
is never followed, so the login cannot be taken anywhere else (KAV-123).

The server these tests talk to is a real socket on 127.0.0.1 that plays Atlassian well or
badly: it can accept a connection and never answer (what a stalled network does to a client),
hang up, reset the connection, send half an answer, or say "go there instead". Nothing leaves
the laptop, and the only "login" in here is built from pieces, so it can never be mistaken for
a real one.

Each acceptance criterion of KAV-122 has tests here:

    1. a time limit on every request ........ section 1
    2. a clear failure, never the login ..... sections 1, 2 and 5
    3. reads retried, writes never .......... section 1
    4. docs-sync fails, no done line ........ section 6
    5. normal answers unchanged ............. section 3

and each of KAV-123's:

    6. the login never goes to another address ... section 7
    7. a same-address redirect: decided, tested .. section 7
    8. KAV-122's normal cases unchanged .......... sections 1 to 6, and the 304 in section 7
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import traceback
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import atlassian_http as ah
import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

EMAIL = "someone@example.invalid"
TOKEN = "-".join(["not", "a", "real", "token"])  # built from pieces, as for any fake secret
LOGIN = "Basic " + base64.b64encode(f"{EMAIL}:{TOKEN}".encode()).decode()
HEADERS = {"Authorization": LOGIN, "Accept": "application/json", "Content-Type": "application/json"}

SILENT = "silent"  # read the request, then say nothing and keep the connection open
DROP = "drop"  # read the request, then hang up without a word
RESET = "reset"  # read the request, then reset the connection
Action = bytes | str  # one of the three above, or the bytes of a reply


def reply(status: int, body: str = "", reason: str = "OK") -> bytes:
    payload = body.encode()
    head = (
        f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\n"
        f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n"
    )
    return head.encode() + payload


def half_a_reply(status: int = 200) -> bytes:
    """A reply that promises 100 bytes, sends one, and hangs up."""
    return f"HTTP/1.1 {status} X\r\nContent-Length: 100\r\nConnection: close\r\n\r\n{{".encode()


def redirect(status: int, location: str, reason: str = "Found") -> bytes:
    """An answer that says: go to `location` instead."""
    head = (
        f"HTTP/1.1 {status} {reason}\r\nLocation: {location}\r\n"
        "Content-Length: 0\r\nConnection: close\r\n\r\n"
    )
    return head.encode()


@dataclass
class Seen:
    number: int  # which connection this was, counting from 1
    method: str
    path: str
    headers: dict[str, str]  # names in lower case
    body: bytes


def _read_request(conn: socket.socket, number: int) -> Seen:
    conn.settimeout(5)
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = conn.recv(65536)
        if not chunk:
            raise ConnectionError("the client left before finishing its request")
        data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    method, path, _ = lines[0].split(" ", 2)
    headers = {k.lower(): v.strip() for k, _, v in (line.partition(":") for line in lines[1:])}
    while len(rest) < int(headers.get("content-length", "0")):
        chunk = conn.recv(65536)
        if not chunk:
            break
        rest += chunk
    return Seen(number, method, path, headers, rest)


class FakeAtlassian:
    """A local server that answers each request the way `script` says, and keeps a record."""

    def __init__(self, script: Callable[[Seen], Action]) -> None:
        self.script = script
        self.seen: list[Seen] = []
        self._lock = threading.Lock()
        self._kept: list[socket.socket] = []
        self._stopped = threading.Event()
        self._listener = socket.socket()
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(16)
        self._listener.settimeout(0.1)
        self.base = f"http://127.0.0.1:{self._listener.getsockname()[1]}"
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def close(self) -> None:
        self._stopped.set()
        self._listener.close()
        for conn in self._kept:
            conn.close()

    def wait_for(self, count: int, seconds: float = 3.0) -> None:
        """Wait until `count` requests have been read (the client can be ahead of the server)."""
        deadline = time.monotonic() + seconds
        while len(self.seen) < count and time.monotonic() < deadline:
            time.sleep(0.01)

    def _accept_loop(self) -> None:
        number = 0
        while not self._stopped.is_set():
            try:
                conn, _ = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            number += 1
            threading.Thread(target=self._handle, args=(conn, number), daemon=True).start()

    def _handle(self, conn: socket.socket, number: int) -> None:
        try:
            seen = _read_request(conn, number)
            with self._lock:
                self.seen.append(seen)
            action = self.script(seen)
            if action == SILENT:
                self._kept.append(conn)
                return
            if action == RESET:
                conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            elif isinstance(action, bytes):
                conn.sendall(action)
            conn.close()
        except OSError:
            conn.close()


Start = Callable[[Callable[[Seen], Action]], FakeAtlassian]


@pytest.fixture
def server() -> Iterator[Start]:
    started: list[FakeAtlassian] = []

    def start(script: Callable[[Seen], Action]) -> FakeAtlassian:
        fake = FakeAtlassian(script)
        started.append(fake)
        return fake

    yield start
    for fake in started:
        fake.close()


T = TypeVar("T")


def within(seconds: float, work: Callable[[], T]) -> T:
    """Run `work`. If it is still going after `seconds`, fail instead of hanging the whole run:
    a request that lost its time limit must break the build, not freeze it."""
    values: list[T] = []
    errors: list[Exception] = []

    def target() -> None:
        try:
            values.append(work())
        except Exception as exc:  # handed back to the test, whatever it is
            errors.append(exc)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        pytest.fail(f"still waiting after {seconds:g} seconds: the request has no time limit")
    if errors:
        raise errors[0]
    return values[0]


def ask(
    fake: FakeAtlassian,
    method: str = "GET",
    path: str = "/rest/api/3/myself",
    body: dict[str, Any] | None = None,
    **settings: Any,
) -> tuple[int, dict[str, Any]]:
    settings = {"timeout": 5.0, "retry_wait": 0.0} | settings
    return ah.send(method, fake.base, path, HEADERS, body, **settings)


# ── 1. a time limit on every request; reads retried, writes never ────────────


def test_a_server_that_never_answers_ends_a_read_in_time_and_it_is_tried_three_times(
    server: Start,
) -> None:
    fake = server(lambda seen: SILENT)
    started = time.monotonic()
    with pytest.raises(ah.AtlassianUnreachable) as caught:
        within(5, lambda: ask(fake, timeout=0.3))
    assert time.monotonic() - started < 5
    fake.wait_for(3)
    assert len(fake.seen) == 3
    message = str(caught.value)
    assert "GET /rest/api/3/myself" in message
    assert "no reply within 0.3 seconds" in message
    assert "tried 3 times" in message


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
def test_a_write_that_gets_no_answer_is_not_repeated(server: Start, method: str) -> None:
    fake = server(lambda seen: SILENT)
    with pytest.raises(ah.AtlassianUnreachable) as caught:
        within(5, lambda: ask(fake, method, "/rest/api/3/issue", {"fields": {}}, timeout=0.3))
    fake.wait_for(1)
    assert len(fake.seen) == 1
    message = str(caught.value)
    assert f"{method} /rest/api/3/issue" in message
    assert "may already have gone through" in message
    assert "tried" not in message


@pytest.mark.parametrize("hang_up", [DROP, RESET])
def test_a_dropped_connection_fails_clearly_naming_the_request(
    server: Start, hang_up: str
) -> None:
    fake = server(lambda seen: hang_up)
    with pytest.raises(ah.AtlassianUnreachable) as read:
        ask(fake)
    with pytest.raises(ah.AtlassianUnreachable) as write:
        ask(fake, "POST", "/rest/api/3/issue", {})
    fake.wait_for(4)
    assert [s.method for s in fake.seen] == ["GET", "GET", "GET", "POST"]
    assert "GET /rest/api/3/myself" in str(read.value)
    assert "POST /rest/api/3/issue" in str(write.value)


def test_half_an_answer_is_no_answer(server: Start) -> None:
    fake = server(lambda seen: half_a_reply())
    with pytest.raises(ah.AtlassianUnreachable) as caught:
        ask(fake)
    assert "GET /rest/api/3/myself" in str(caught.value)
    assert len(fake.seen) == 3


def test_a_refused_connection_is_no_answer() -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.close()  # nothing listens on this port now
    with pytest.raises(ah.AtlassianUnreachable) as caught:
        within(
            5,
            lambda: ah.send(
                "GET", f"http://127.0.0.1:{port}", "/rest/api/3/myself", HEADERS,
                timeout=0.3, retry_wait=0.0,
            ),
        )
    assert "GET /rest/api/3/myself" in str(caught.value)


def test_a_read_that_fails_once_and_then_works_returns_the_answer(
    server: Start, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = server(lambda seen: DROP if seen.number == 1 else reply(200, '{"name": "Roshan"}'))
    assert ask(fake) == (200, {"name": "Roshan"})
    assert len(fake.seen) == 2
    assert "trying again (2 of 3)" in capsys.readouterr().err


def test_a_read_waits_a_little_longer_before_each_new_try(server: Start) -> None:
    fake = server(lambda seen: DROP)
    pauses: list[float] = []
    with pytest.raises(ah.AtlassianUnreachable):
        ask(fake, retry_wait=1.0, sleep=pauses.append)
    assert pauses == [1.0, 3.0]


def test_the_limit_and_the_pause_can_be_set_from_the_environment(
    server: Start, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KAVAL_ATLASSIAN_TIMEOUT", "0.3")
    monkeypatch.setenv("KAVAL_ATLASSIAN_RETRY_WAIT", "0")
    fake = server(lambda seen: SILENT)
    # The built-in 30 seconds times three tries would not fit in the 5 this test allows.
    with pytest.raises(ah.AtlassianUnreachable):
        within(5, lambda: ah.send("GET", fake.base, "/rest/api/3/myself", HEADERS))


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("KAVAL_ATLASSIAN_TIMEOUT", "soon"),
        ("KAVAL_ATLASSIAN_TIMEOUT", "0"),
        ("KAVAL_ATLASSIAN_TIMEOUT", "-5"),
        ("KAVAL_ATLASSIAN_RETRY_WAIT", "later"),
        ("KAVAL_ATLASSIAN_RETRY_WAIT", "-1"),
    ],
)
def test_a_bad_setting_is_refused_by_name_before_anything_is_sent(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        ah.send("GET", "http://127.0.0.1:9", "/rest/api/3/myself", HEADERS)


# ── 2. the failure never shows the login ─────────────────────────────────────


def test_the_failure_never_shows_the_login(
    server: Start, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = server(lambda seen: SILENT)
    with pytest.raises(ah.AtlassianUnreachable) as caught:
        ask(fake, timeout=0.3)
    fake.wait_for(1)
    shown = "\n".join(
        [
            str(caught.value),
            repr(caught.value),
            "".join(traceback.format_exception(caught.value)),
            capsys.readouterr().err,
        ]
    )
    assert LOGIN not in shown
    assert TOKEN not in shown
    assert "Authorization" not in str(caught.value)
    assert fake.seen[0].headers["authorization"] == LOGIN  # it was sent; it is never shown


# ── 3. normal answers are unchanged ──────────────────────────────────────────


def test_a_200_comes_back_as_status_and_body(server: Start) -> None:
    fake = server(lambda seen: reply(200, '{"key": "KAV-1"}'))
    assert ask(fake) == (200, {"key": "KAV-1"})


def test_a_204_with_nothing_in_it_comes_back_with_an_empty_body(server: Start) -> None:
    fake = server(lambda seen: reply(204, "", "No Content"))
    assert ask(fake, "PUT", "/rest/api/3/issue/KAV-1", {"fields": {}}) == (204, {})


def test_a_write_sends_its_body_as_json_with_the_login(server: Start) -> None:
    fake = server(lambda seen: reply(201, '{"id": "9"}', "Created"))
    payload = {"fields": {"summary": "hello"}}
    assert ask(fake, "POST", "/rest/api/3/issue", payload) == (201, {"id": "9"})
    seen = fake.seen[0]
    assert json.loads(seen.body) == payload
    assert seen.headers["content-type"] == "application/json"
    assert seen.headers["authorization"] == LOGIN


def test_a_4xx_comes_back_as_status_and_body_and_is_not_retried(server: Start) -> None:
    fake = server(lambda seen: reply(404, '{"errorMessages": ["No such issue"]}', "Not Found"))
    assert ask(fake) == (404, {"errorMessages": ["No such issue"]})
    assert len(fake.seen) == 1


def test_a_4xx_that_is_not_json_keeps_its_text(server: Start) -> None:
    fake = server(lambda seen: reply(400, "<html>Bad request</html>", "Bad Request"))
    assert ask(fake) == (400, {"raw": "<html>Bad request</html>"})


def test_a_5xx_is_an_answer_so_it_is_returned_and_not_retried(server: Start) -> None:
    fake = server(lambda seen: reply(503, '{"message": "maintenance"}', "Service Unavailable"))
    assert ask(fake) == (503, {"message": "maintenance"})
    assert len(fake.seen) == 1


def test_an_error_answer_cut_short_still_returns_its_status(server: Start) -> None:
    fake = server(lambda seen: half_a_reply(500))
    assert ask(fake) == (500, {"raw": ""})


# ── 4. what a script does with a failure ─────────────────────────────────────


def test_guard_turns_no_answer_into_one_plain_line() -> None:
    def main() -> None:
        raise ah.AtlassianUnreachable("GET", "/x", "no reply within 30 seconds", 3)

    with pytest.raises(SystemExit) as caught:
        ah.guard(main)
    assert caught.value.code == (
        "GET /x: no answer from Atlassian (no reply within 30 seconds), tried 3 times"
    )


def test_guard_lets_everything_else_through() -> None:
    def bug() -> None:
        raise ValueError("a real bug")

    ah.guard(lambda: None)
    with pytest.raises(SystemExit) as stopped:
        ah.guard(lambda: sys.exit(2))
    assert stopped.value.code == 2
    with pytest.raises(ValueError, match="a real bug"):
        ah.guard(bug)


# ── 5. the real scripts, run as they are run, in a throwaway copy of the repo ─

SCRIPTS = (
    "atlassian.py",
    "atlassian_http.py",
    "jira_adf.py",
    "jira_release.py",
    "jira-sync.py",
    "jira-dashboards.py",
    "jira-dashboards.toml",
    "publish-confluence.py",
)


def make_repo(tmp_path: Path, base: str) -> Path:
    """The real scripts, copied, with a login that leads to `base` instead of Atlassian."""
    root = tmp_path / "repo"
    tracking = root / "scripts" / "tracking"
    tracking.mkdir(parents=True)
    for name in SCRIPTS:
        shutil.copy(HERE / name, tracking / name)
    # The real dashboard step rewrites docs/dashboard.html; a stand-in lets `make docs-sync`
    # run on to the Confluence step, which is the one under test.
    (tracking / "dashboard.py").write_text('print("dashboard step (stand-in)")\n', encoding="utf-8")
    shutil.copy(REPO / "Makefile", root / "Makefile")
    (root / "docs").mkdir()
    (root / "docs" / "repo-guide.md").write_text("# Repo guide\n\nOne line.\n", encoding="utf-8")
    (root / ".env").write_text(
        f"JIRA_SITE_URL={base}\nJIRA_EMAIL={EMAIL}\nJIRA_API_TOKEN={TOKEN}\n", encoding="utf-8"
    )
    return root


def run_in(root: Path, argv: list[str], **settings: str) -> subprocess.CompletedProcess[str]:
    env = os.environ | {
        "KAVAL_ATLASSIAN_TIMEOUT": "0.5",
        "KAVAL_ATLASSIAN_RETRY_WAIT": "0",
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
    }
    env.pop("PYTHONPATH", None)  # the copy must import its own modules, not the repo's
    env.update(settings)
    return subprocess.run(argv, cwd=root, env=env, capture_output=True, text=True, timeout=60)


def confluence_answers(seen: Seen) -> Action:
    """Enough of Confluence for publish-confluence.py to run from start to finish."""
    if seen.method == "GET" and seen.path.startswith("/wiki/api/v2/spaces?keys="):
        return reply(200, '{"results": [{"id": "1"}]}')
    if seen.method == "GET" and "/pages?title=" in seen.path:
        return reply(200, '{"results": [{"id": "7", "version": {"number": 3}}]}')
    if seen.method == "PUT":
        return reply(200, '{"id": "7"}')
    return reply(404, '{"message": "not part of this test"}', "Not Found")


QUIET = {
    "publish-confluence.py": ([], "/wiki/api/v2/spaces?keys=KAV"),
    "jira-sync.py": (["show", "KAV-6"], "/rest/api/3/search/jql"),
    "jira-dashboards.py": ([], "/rest/api/3/dashboard/gadgets"),
}


@pytest.mark.parametrize("script", sorted(QUIET))
def test_each_script_stops_with_one_plain_line_when_atlassian_goes_quiet(
    server: Start, tmp_path: Path, script: str
) -> None:
    fake = server(lambda seen: SILENT)
    args, path = QUIET[script]
    root = make_repo(tmp_path, fake.base)
    done = run_in(root, [sys.executable, f"scripts/tracking/{script}", *args])
    shown = done.stdout + done.stderr
    assert done.returncode != 0
    assert f"GET {path}" in done.stderr
    assert "no answer from Atlassian" in done.stderr
    assert "tried 3 times" in done.stderr
    assert "Traceback" not in done.stderr
    assert TOKEN not in shown
    assert LOGIN not in shown
    fake.wait_for(3)
    assert len(fake.seen) == 3


def test_call_sends_the_login_and_returns_status_and_body(server: Start, tmp_path: Path) -> None:
    fake = server(lambda seen: reply(200, '{"displayName": "Someone"}'))
    code = (
        "import sys; sys.path.insert(0, 'scripts/tracking'); import atlassian; "
        "print(atlassian.call('GET', '/rest/api/3/myself'))"
    )
    done = run_in(
        make_repo(tmp_path, fake.base), [sys.executable, "-c", code], KAVAL_ATLASSIAN_TIMEOUT="10"
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "(200, {'displayName': 'Someone'})"
    assert fake.seen[0].headers["authorization"] == LOGIN
    assert TOKEN not in done.stdout + done.stderr


def test_a_page_that_will_not_update_fails_the_sync_after_the_others_were_tried(
    server: Start, tmp_path: Path
) -> None:
    def script(seen: Seen) -> Action:
        if seen.method == "PUT":
            return reply(500, '{"message": "boom"}', "Server Error")
        return confluence_answers(seen)

    fake = server(script)
    done = run_in(
        make_repo(tmp_path, fake.base),
        [sys.executable, "scripts/tracking/publish-confluence.py"],
        KAVAL_ATLASSIAN_TIMEOUT="10",
    )
    assert done.returncode != 0
    assert "UPDATE FAILED" in done.stdout
    assert "done. space home" not in done.stdout
    assert "were not updated" in done.stderr
    assert len([s for s in fake.seen if s.method == "PUT"]) >= 5  # it went on past the first one


# ── 6. `make docs-sync` ──────────────────────────────────────────────────────

needs_make = pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed here")


@needs_make
def test_make_docs_sync_fails_and_does_not_say_it_is_done(server: Start, tmp_path: Path) -> None:
    fake = server(lambda seen: SILENT)
    done = run_in(make_repo(tmp_path, fake.base), ["make", "docs-sync"])
    shown = done.stdout + done.stderr
    assert "dashboard step (stand-in)" in done.stdout  # it got as far as the Confluence step
    assert done.returncode != 0
    assert "no answer from Atlassian" in done.stderr
    assert "done. space home" not in shown
    assert "Now republish" not in shown


@needs_make
def test_make_docs_sync_says_it_is_done_when_atlassian_answers(
    server: Start, tmp_path: Path
) -> None:
    """The other half of the proof: the harness above is not simply failing for every reason."""
    fake = server(confluence_answers)
    done = run_in(
        make_repo(tmp_path, fake.base), ["make", "docs-sync"], KAVAL_ATLASSIAN_TIMEOUT="10"
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "done. space home" in done.stdout
    assert "Now republish" in done.stdout


def test_the_docs_sync_recipe_does_not_swallow_a_failure() -> None:
    lines = (REPO / "Makefile").read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("docs-sync:"))
    steps: list[str] = []
    for line in lines[start + 1 :]:
        if not line.startswith("\t"):
            break
        steps.append(line.strip())
    publish = next(i for i, step in enumerate(steps) if "publish-confluence.py" in step)
    done = next(i for i, step in enumerate(steps) if "Now republish" in step)
    assert publish < done, "the done line must come after the Confluence step"
    for step in steps:
        prefix = step[: len(step) - len(step.lstrip("@-+"))]
        assert "-" not in prefix, f"a leading - makes make carry on after a failure: {step}"
        assert "|| true" not in step
        assert "; true" not in step
        assert "|| :" not in step


# ── 7. a redirect is never followed (KAV-123) ────────────────────────────────

# The answers Python's own redirect handler follows, taking the Authorization header with it.
REDIRECT_STATUSES = [301, 302, 303, 307, 308]


def nothing_came(fake: FakeAtlassian) -> None:
    """Wait a moment for a request that must not come, then check that none did."""
    fake.wait_for(1, seconds=0.3)
    assert fake.seen == [], f"a request reached a server it should never have reached: {fake.seen}"


@pytest.mark.parametrize("status", REDIRECT_STATUSES)
def test_a_redirect_to_another_address_never_takes_the_login_there(
    server: Start, status: int
) -> None:
    elsewhere = server(lambda seen: reply(200, '{"reached": "yes"}'))
    fake = server(lambda seen: redirect(status, f"{elsewhere.base}/rest/api/3/myself"))
    with contextlib.suppress(Exception):  # how the call ends is the next test's business
        ask(fake)
    nothing_came(elsewhere)  # not the login, and not even a request
    assert len(fake.seen) == 1
    assert fake.seen[0].headers["authorization"] == LOGIN  # the login went where it belongs


@pytest.mark.parametrize("status", REDIRECT_STATUSES)
def test_a_redirect_is_refused_with_one_plain_line_and_not_tried_again(
    server: Start, status: int
) -> None:
    elsewhere = server(lambda seen: reply(200, "{}"))
    fake = server(lambda seen: redirect(status, f"{elsewhere.base}/somewhere"))
    with pytest.raises(ah.AtlassianRedirected) as caught:
        ask(fake)
    fake.wait_for(2, seconds=0.3)
    assert len(fake.seen) == 1  # it is an answer, so a read is not tried three times
    assert str(caught.value).startswith(
        f"GET /rest/api/3/myself: Atlassian answered with a redirect (HTTP {status}). "
        "It was not followed, so the login stays on this site."
    )


def test_a_redirect_to_the_same_address_is_not_followed_either(server: Start) -> None:
    def script(seen: Seen) -> Action:
        if seen.number == 1:  # back to the very address it came to, read from its Host header
            return redirect(301, f"http://{seen.headers['host']}/rest/api/3/moved")
        return reply(200, '{"followed": true}')

    fake = server(script)
    with pytest.raises(ah.AtlassianRedirected):
        ask(fake)
    fake.wait_for(2, seconds=0.3)
    assert [s.path for s in fake.seen] == ["/rest/api/3/myself"]


@pytest.mark.parametrize(
    ("method", "status"),
    [("POST", 301), ("POST", 302), ("POST", 303), ("POST", 307), ("PUT", 307), ("DELETE", 307)],
)
def test_a_write_that_is_redirected_is_not_followed_and_not_repeated(
    server: Start, method: str, status: int
) -> None:
    elsewhere = server(lambda seen: reply(200, "{}"))
    fake = server(lambda seen: redirect(status, f"{elsewhere.base}/rest/api/3/issue"))
    with pytest.raises(ah.AtlassianRedirected) as caught:
        ask(fake, method, "/rest/api/3/issue", {"fields": {"summary": "hello"}})
    fake.wait_for(2, seconds=0.3)
    assert len(fake.seen) == 1
    nothing_came(elsewhere)
    message = str(caught.value)
    assert message.startswith(f"{method} /rest/api/3/issue: Atlassian answered with a redirect")
    assert "may already have gone through" in message  # a 303 can follow a write that landed


def test_the_redirect_message_never_shows_the_login_or_where_it_pointed(
    server: Start, capsys: pytest.CaptureFixture[str]
) -> None:
    marker = "-".join(["signed", "link", "marker"])  # stands for a token in a redirect's address
    elsewhere = server(lambda seen: reply(200, "{}"))
    fake = server(lambda seen: redirect(302, f"{elsewhere.base}/{marker}?sig={marker}"))
    with pytest.raises(ah.AtlassianRedirected) as caught:
        ask(fake)
    shown = "\n".join(
        [
            str(caught.value),
            repr(caught.value),
            "".join(traceback.format_exception(caught.value)),
            capsys.readouterr().err,
        ]
    )
    for hidden in (LOGIN, TOKEN, marker, elsewhere.base):
        assert hidden not in shown
    assert "Authorization" not in str(caught.value)


def test_a_304_is_still_an_ordinary_answer_and_not_a_redirect(server: Start) -> None:
    """Only the answers that point somewhere else are refused; every other status is unchanged."""
    fake = server(lambda seen: reply(304, "", "Not Modified"))
    assert ask(fake) == (304, {"raw": ""})
    assert len(fake.seen) == 1


def test_guard_turns_a_redirect_into_one_plain_line() -> None:
    def main() -> None:
        raise ah.AtlassianRedirected("GET", "/x", 302)

    with pytest.raises(SystemExit) as caught:
        ah.guard(main)
    assert caught.value.code == (
        "GET /x: Atlassian answered with a redirect (HTTP 302). It was not followed, so the "
        "login stays on this site. If the site has moved, update JIRA_SITE_URL in .env."
    )


@pytest.mark.parametrize("script", sorted(QUIET))
def test_each_script_stops_at_a_redirect_and_creates_nothing(
    server: Start, tmp_path: Path, script: str
) -> None:
    """The reason a redirect is a stop and not an answer for the script to read: Confluence's
    script takes any answer but a 200 for "that page is not there" and goes on to create it."""
    elsewhere = server(lambda seen: reply(200, '{"results": []}'))
    fake = server(lambda seen: redirect(302, f"{elsewhere.base}{seen.path}"))
    args, path = QUIET[script]
    root = make_repo(tmp_path, fake.base)
    done = run_in(root, [sys.executable, f"scripts/tracking/{script}", *args])
    shown = done.stdout + done.stderr
    assert done.returncode != 0
    assert f"GET {path}" in done.stderr
    assert "answered with a redirect" in done.stderr
    assert "Traceback" not in done.stderr
    assert TOKEN not in shown
    assert LOGIN not in shown
    fake.wait_for(2, seconds=0.3)
    assert [s.method for s in fake.seen] == ["GET"]  # it stopped there: nothing was created
    nothing_came(elsewhere)
