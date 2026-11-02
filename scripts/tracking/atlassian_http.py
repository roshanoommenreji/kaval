"""One request to Atlassian, with a time limit and a plain failure when nothing answers (KAV-122).

`call()` in atlassian.py hands every Jira and Confluence request to `send()` here. Stdlib only.
The login lives in atlassian.py; this file only ever sees it as a header it passes on.

What it promises:

* Every wait (connecting, waiting for the reply, reading it) is limited to TIMEOUT seconds, so a
  stalled connection fails instead of waiting for ever. The limit is per wait, as with any HTTP
  client; a server that drips a byte a minute is not what stalled connections look like.
* A read (GET) that gets no answer is tried READ_TRIES times in all, pausing RETRY_WAIT seconds
  and then three times that. Reads change nothing, so repeating one is always safe.
* A write (POST, PUT, DELETE) is never repeated by itself. One that timed out may have landed,
  and repeating it could make a second Jira issue or a Confluence version conflict.
* No answer at all (timed out, refused, dropped, name not found, a reply that broke off) raises
  AtlassianUnreachable. Its message names the method and the path and nothing else: no header,
  so never the login.
* An answer, whatever its status, is returned as (status, body): never raised, never retried.

Both numbers can be set for one run, e.g. `KAVAL_ATLASSIAN_TIMEOUT=120 make docs-sync` on a slow
connection; a value that is not a number of seconds is refused by name before anything is sent.
"""
from __future__ import annotations

import http.client
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

TIMEOUT = 30.0  # seconds each wait may take
RETRY_WAIT = 1.0  # seconds before a read's second try; three times that before its third
READ_TRIES = 3

# What "no answer" looks like from here. URLError (a failed connection or lookup) is an OSError,
# like a timeout or a reset; HTTPException covers a reply that never began or broke off.
_NO_ANSWER = (OSError, http.client.HTTPException)


class AtlassianUnreachable(RuntimeError):
    """Nothing came back: the request timed out, was refused or dropped, or the reply broke off."""

    def __init__(self, method: str, path: str, what: str, tries: int) -> None:
        self.method, self.path, self.what, self.tries = method, path, what, tries
        message = f"{method} {path}: no answer from Atlassian ({what})"
        if method == "GET":
            message += f", tried {tries} times" if tries > 1 else ""
        else:
            message += (
                ". Not repeated: it may already have gone through, "
                "so look before running it again"
            )
        super().__init__(message)


def _seconds(name: str, default: float, *, above_zero: bool) -> float:
    """A number of seconds from the environment, or `default`; anything else is refused by name."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not (math.isfinite(value) and (value > 0 if above_zero else value >= 0)):
        needs = "a number of seconds above zero" if above_zero else "a number of seconds"
        raise ValueError(f"{name} must be {needs}, not {raw!r}")
    return value


def _what(exc: BaseException, limit: float) -> str:
    """What went wrong, in the error's own plain words, with nothing about the request in it."""
    reason: object = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, TimeoutError):
        return f"no reply within {limit:g} seconds"
    if isinstance(reason, str):
        return reason
    return f"{type(reason).__name__}: {reason}"


def _error_body(exc: urllib.error.HTTPError) -> dict[str, Any]:
    """The body of an error answer. The status has arrived, so a body that breaks off is not a
    failure: the caller still gets the status."""
    try:
        raw = exc.read()
    except _NO_ANSWER:
        return {"raw": ""}
    finally:
        exc.close()
    try:
        parsed: dict[str, Any] = json.loads(raw)
        return parsed
    except json.JSONDecodeError:
        return {"raw": raw.decode(errors="replace")[:800]}


def _once(request: urllib.request.Request, limit: float) -> tuple[int, dict[str, Any]]:
    try:
        with urllib.request.urlopen(request, timeout=limit) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        return exc.code, _error_body(exc)


def send(
    method: str,
    base: str,
    path: str,
    headers: Mapping[str, str],
    body: dict[str, Any] | None = None,
    *,
    timeout: float | None = None,
    retry_wait: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[int, dict[str, Any]]:
    """Make one request. Returns (status, parsed body); raises AtlassianUnreachable if nothing
    answers. `path` is what follows the site address, and is all a message ever shows of it."""
    limit = timeout if timeout is not None else _seconds(
        "KAVAL_ATLASSIAN_TIMEOUT", TIMEOUT, above_zero=True
    )
    pause = retry_wait if retry_wait is not None else _seconds(
        "KAVAL_ATLASSIAN_RETRY_WAIT", RETRY_WAIT, above_zero=False
    )
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        f"{base}{path}", data=data, headers=dict(headers), method=method
    )
    tries = READ_TRIES if method == "GET" else 1
    attempt = 0
    while True:
        attempt += 1
        try:
            return _once(request, limit)
        except _NO_ANSWER as exc:
            what = _what(exc, limit)
            if attempt >= tries:
                raise AtlassianUnreachable(method, path, what, tries) from None
            print(
                f"{method} {path}: no answer ({what}); trying again ({attempt + 1} of {tries})",
                file=sys.stderr,
            )
            sleep(pause * 3 ** (attempt - 1))


def guard(main: Callable[[], None]) -> None:
    """Run a script's main(). If Atlassian does not answer, end with that one plain line and exit
    code 1, not a traceback. Anything else, including a script's own sys.exit, passes through."""
    try:
        main()
    except AtlassianUnreachable as exc:
        sys.exit(str(exc))
