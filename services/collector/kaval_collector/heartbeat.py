"""The "still alive" ping to an outside dead-man's-switch (KAV-83, ADR-0052).

Everything Kaval does to notice trouble lives inside the system it watches. If the server, the
cluster or the poller itself is gone, nothing inside can say so. The answer is the oldest trick in
monitoring: an outside service expects a small ping every few minutes and raises the alarm when the
pings STOP, so the silence is the signal and it needs nothing from the thing that has died.

What this does, and does not do:

- The poller calls `Heartbeat.beat()` after a pass that fully worked (Prometheus answered, the
  database took the writes). A pass that failed sends nothing, so a poller that is up but not
  working goes quiet and is noticed, which is the point.
- At most one ping per `INTERVAL_SECONDS`; after a failed ping, one retry per `RETRY_SECONDS`.
- **A ping that fails never stops Kaval.** `send` never raises and the loop carries on; a
  monitoring service being down must not take the poller down with it. A change (first success,
  failing, working again) is printed once, so the log says it without repeating every 30 seconds.
- **The address is a secret** (anyone who has it can keep a dead system looking alive), so it comes
  from the deployment's settings only, is never printed or put in an error message, must be
  `https`, and a reply that says "go to this other address" is not followed.
- Empty address = off. That is the default, and what a missing Secret gives.

The same address is also pinged by `scripts/ops/still_alive.py` while production is parked (no
poller is running then), so a parked production does not look like a dead one.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

INTERVAL_SECONDS = 120.0
RETRY_SECONDS = 60.0
TIMEOUT = 5.0
USER_AGENT = "kaval-heartbeat"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


def check_url(url: str) -> str:
    """The address as given, or "" for off. Loud on anything that is not a plain https address:
    a typo in a setting must stop the start-up, not quietly ping nothing. The message never
    contains the address."""
    url = url.strip()
    if not url:
        return ""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("the heartbeat address must be an https:// address")
    if parsed.username or parsed.password:
        raise ValueError("the heartbeat address must not carry a user name or password")
    return url


def send(url: str, timeout: float = TIMEOUT, opener: Any = None) -> str | None:
    """One ping. None means it worked; otherwise a short reason that never contains the address.
    Never raises."""
    active = opener or urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with active.open(request, timeout=timeout) as response:
            response.read(64)  # the service answers "OK"; read a little and throw it away
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code}"  # a redirect is an HTTPError here, because it is not followed
    except TimeoutError:
        return "timed out"
    except urllib.error.URLError as exc:
        return "timed out" if isinstance(exc.reason, TimeoutError) else "could not connect"
    except Exception as exc:  # noqa: BLE001 - nothing here may stop the poller
        return f"failed ({type(exc).__name__})"
    return None


class Heartbeat:
    """Throttled pinging for one loop. Carried between passes, like the poller's `Health`."""

    def __init__(
        self, url: str, *, every: float = INTERVAL_SECONDS, retry: float = RETRY_SECONDS,
        sender: Callable[[str], str | None] = send, clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = check_url(url)
        self._every, self._retry = every, retry
        self._send, self._clock = sender, clock
        self._due_at: float | None = None
        self._failing: bool | None = None  # None = nothing sent yet

    @property
    def enabled(self) -> bool:
        return bool(self._url)

    def beat(self) -> list[str]:
        """Call after a pass that fully worked. Pings if one is due; returns lines to print, only
        when something changed."""
        if not self._url:
            return []
        now = self._clock()
        if self._due_at is not None and now < self._due_at:
            return []
        reason = self._send(self._url)
        self._due_at = now + (self._retry if reason else self._every)
        was, self._failing = self._failing, reason is not None
        if reason is not None:
            return [] if was else [f"heartbeat: pings are failing ({reason}); "
                                   "Kaval carries on, but the outside check will go quiet"]
        if was is None:
            return ["heartbeat: first ping sent"]
        return ["heartbeat: pings are working again"] if was else []
