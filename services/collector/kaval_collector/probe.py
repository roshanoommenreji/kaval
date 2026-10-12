"""A small synthetic request that notices a slow service (KAV-81, ADR-0047).

Prometheus and the Kubernetes events say a program is up; neither says it has become slow. The
only honest way to know is to ask it something and time the answer, which is what a user does.
This runs inside the existing Prometheus poller's loop (`kaval_collector.prometheus`), not as a
new program: one more thing it does each pass, with no new Deployment, token or image.

What counts as slow, and what does not:

- **Slow** is an answer that took longer than `SLOW_SECONDS`, or no answer within `TIMEOUT`. A
  request that never comes back is the slowest request there is, so a timeout is slow, not down.
- **Not slow:** a fast answer of any kind (even an error page: that is the job of the "is it up"
  checks), and a refused connection (nothing is listening: that is down, not slow).
- **Three in a row** (`SLOW_AFTER`). One slow answer is a garbage collection or a busy second;
  a person is told only when it keeps happening. Counted per target, between passes.

Safety: the addresses to ask come from the deployment's settings and nowhere else. They are never
taken from a signal, an incident or a model answer, so nothing that can be influenced from outside
can make the collector fetch an address of its choosing. Only `http`/`https`; redirects are not
followed (a service that answers "go to this other address" is not followed there); at most
`MAX_TARGETS`; the body is read only up to a small cap and thrown away.

Like the memory and alert signals, a slow service is written once and then renewed every
`renew_seconds` while it stays slow, so the correlator's 15-minute quiet window keeps the incident
open exactly as long as the slowness lasts.
"""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from kaval_shared.models import Signal
from sqlalchemy import select
from sqlalchemy.orm import Session

KIND = "service_slow"
SOURCE = "prometheus"  # the poller's source: the alerting reads this source's signals
SLOW_SECONDS = 1.0
TIMEOUT = 5.0
SLOW_AFTER = 3
MAX_TARGETS = 5
BODY_CAP = 64 * 1024

_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}/[a-z0-9][a-z0-9-]{0,62}$")

Outcome = Literal["answered", "timeout", "unreachable"]


@dataclass(frozen=True)
class Target:
    name: str  # namespace/service, the signal's target
    url: str


@dataclass(frozen=True)
class Answer:
    seconds: float
    outcome: Outcome


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


Fetch = Callable[[str, float], Answer]


def parse_targets(spec: str) -> list[Target]:
    """`ns/name=http://host:port/path,ns/name2=http://...` -> targets. Loud on anything odd:
    a typo in a setting must stop the start-up, not quietly check nothing."""
    targets: list[Target] = []
    for part in (p.strip() for p in spec.split(",")):
        if not part:
            continue
        name, sep, url = part.partition("=")
        if not sep or not _NAME.match(name):
            raise ValueError(f"probe target {part!r}: expected namespace/name=url")
        if urllib.parse.urlparse(url).scheme not in ("http", "https"):
            raise ValueError(f"probe target {name}: not an http(s) URL")
        targets.append(Target(name, url))
    if len(targets) > MAX_TARGETS:
        raise ValueError(f"at most {MAX_TARGETS} probe targets, got {len(targets)}")
    return targets


def fetch(url: str, timeout: float = TIMEOUT) -> Answer:
    """One timed GET. Any answer counts as answered, whatever its status: speed is measured here,
    correctness is not. The clock covers the whole answer, not just the first byte."""
    opener = urllib.request.build_opener(_NoRedirect)
    started = time.monotonic()
    try:
        with opener.open(url, timeout=timeout) as response:
            response.read(BODY_CAP)
    except urllib.error.HTTPError:
        pass  # a fast error page is still an answer; the status is not this check's business
    except TimeoutError:
        return Answer(time.monotonic() - started, "timeout")
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            return Answer(time.monotonic() - started, "timeout")
        return Answer(time.monotonic() - started, "unreachable")
    except OSError:
        return Answer(time.monotonic() - started, "unreachable")
    return Answer(time.monotonic() - started, "answered")


@dataclass
class Slowness:
    """How many passes in a row each target has been slow. Carried between passes, like the
    poller's `Health`, so a single slow answer is not mistaken for a slow service."""

    in_a_row: dict[str, int] = field(default_factory=dict)


def is_slow(answer: Answer, slow_seconds: float) -> bool:
    if answer.outcome == "timeout":
        return True
    return answer.outcome == "answered" and answer.seconds > slow_seconds


def _written_since(session: Session, target: str, since: datetime) -> bool:
    return session.scalar(
        select(Signal.id).where(
            Signal.source == SOURCE, Signal.kind == KIND, Signal.target == target,
            Signal.observed_at >= since,
        ).limit(1)
    ) is not None


def poll(
    session: Session, targets: Sequence[Target], state: Slowness, *,
    fetcher: Fetch = fetch, slow_seconds: float = SLOW_SECONDS, slow_after: int = SLOW_AFTER,
    renew_seconds: float = 300.0, timeout: float = TIMEOUT, now: datetime | None = None,
) -> list[str]:
    """One pass: ask each target, count how long it has been slow, and write a signal once it has
    been slow `slow_after` passes in a row (renewed every `renew_seconds` while it stays so)."""
    now = now or datetime.now(UTC)
    lines: list[str] = []
    for t in targets:
        answer = fetcher(t.url, timeout)
        if not is_slow(answer, slow_seconds):
            state.in_a_row[t.name] = 0
            continue
        state.in_a_row[t.name] = state.in_a_row.get(t.name, 0) + 1
        if state.in_a_row[t.name] < slow_after:
            continue
        if _written_since(session, t.name, now - timedelta(seconds=renew_seconds)):
            continue
        session.add(Signal(
            source=SOURCE, kind=KIND, target=t.name, observed_at=now,
            # Numbers and a fixed word only: nothing the target said is ever copied into the row.
            value={"seconds": round(answer.seconds, 2), "outcome": answer.outcome,
                   "slow_over_seconds": slow_seconds, "passes_in_a_row": state.in_a_row[t.name]},
        ))
        lines.append(f"{KIND:28} {t.name}  {answer.seconds:.1f}s ({answer.outcome})")
    session.commit()
    return lines
