"""Context assembly for one incident (KAV-40, ADR-0015): what a human on call would read
first, before being asked to diagnose anything.

Three sources, matching `docs/learn/phase-2-the-agent-loop.md`'s "RAG, and why not
fine-tuning" section:

- **Runbooks**: the `## ` sections whose embedding is closest to the incident's, from
  `runbook_chunk` (kept current by `kaval_agent.index_runbooks`).
- **Similar past incidents, with their outcome**: closed incidents sharing this one's cause
  and domain — exact fingerprint (a recurrence) ranked first — left-joined with whatever was
  measured. No embedding needed: the fingerprint already says what kind of failure this is,
  more precisely than a similarity score would.
- **Recent changes**: optional, supplied by the caller (`kaval_agent.recent_changes`, a
  laptop-only tool — see that module's docstring for why). `None` here means "not checked",
  which `Context.render()` renders differently from "checked, found nothing".

Everything here reads; nothing writes. Building a context is not a step the agent needs
write access for, same as correlation.

    python -m kaval_agent.context <incident-id> [--with-changes]
"""

from __future__ import annotations

import argparse
import sys
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from kaval_shared.models import Incident, Outcome, RunbookChunk, Signal
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from kaval_agent.embeddings import embed_one
from kaval_agent.recent_changes import RecentChanges

DEFAULT_RUNBOOK_LIMIT = 5  # the learn page's "3-5 retrieved runbook sections"
DEFAULT_HISTORY_LIMIT = 3  # "2-3 similar past incidents"
# Below this, a runbook chunk is noise, not a match. Measured on the dev server against the
# one runbook that exists today (Lab 10): a genuine match — a database-connection query
# against "database lost or corrupted" — scored 0.69; unrelated queries (an OOM crashloop, an
# idle volume) against the *same* runbook scored 0.38-0.40. all-minilm's floor for any two
# short technical chunks in this domain sits around 0.35-0.40 just from shared vocabulary
# ("container", "kaval", ...), not real relevance. 0.5 sits in the gap, well clear of both.
# **Recalibrate once a second runbook covering a different failure exists** — one data point
# can't rule out the gap being narrower elsewhere.
MIN_RUNBOOK_SCORE = 0.5


def _cause_domain(fingerprint: str) -> tuple[str, str]:
    cause, domain, _subject = fingerprint.split(":", 2)
    return cause, domain


def _signal_text(signal: Signal) -> str | None:
    for key in ("message", "reason", "alertname"):
        value = signal.value.get(key)
        if value:
            return str(value)
    return None


def query_text_for(incident: Incident, signals: Sequence[Signal]) -> str:
    """A short, embeddable description: the fingerprint in words, plus up to four distinct
    signal messages. Used to find matching runbooks; also worth reading on its own, so
    `Context.render()` shows it first."""
    cause, domain, subject = incident.fingerprint.split(":", 2)
    bits = [f"{cause.replace('_', ' ')} on {subject} ({domain})"]
    seen: set[str] = set()
    for signal in signals:
        text = _signal_text(signal)
        if text and text not in seen:
            seen.add(text)
            bits.append(text)
        if len(seen) >= 4:
            break
    return ". ".join(bits)


@dataclass(frozen=True)
class RunbookMatch:
    path: str
    heading: str
    content: str
    score: float  # 1 - cosine distance; 1.0 is identical, roughly 0 is unrelated


def matching_runbooks(
    session: Session, query_vector: Sequence[float], *,
    limit: int = DEFAULT_RUNBOOK_LIMIT, min_score: float = MIN_RUNBOOK_SCORE,
) -> list[RunbookMatch]:
    distance = RunbookChunk.embedding.cosine_distance(list(query_vector))
    rows = session.execute(
        select(RunbookChunk, distance.label("distance")).order_by(distance).limit(limit)
    ).all()
    matches = [
        RunbookMatch(path=chunk.path, heading=chunk.heading, content=chunk.content, score=1 - d)
        for chunk, d in rows
    ]
    return [m for m in matches if m.score >= min_score]


@dataclass(frozen=True)
class PastIncident:
    incident_id: uuid.UUID
    fingerprint: str
    severity: str
    opened_at: object  # datetime; left loosely typed here, formatted only in render()
    closed_at: object
    resolved: bool | None  # None: no outcome was ever measured
    mttr_sec: int | None
    regression: bool | None


def similar_incidents(
    session: Session, incident: Incident, *, limit: int = DEFAULT_HISTORY_LIMIT
) -> list[PastIncident]:
    """Closed incidents with the same cause and domain, excluding this one. An exact
    recurrence (the same fingerprint — same subject too) ranks first; broader same-kind
    matches fill the rest, most recent first."""
    cause, domain = _cause_domain(incident.fingerprint)
    candidates = session.scalars(
        select(Incident)
        .where(
            Incident.id != incident.id,
            Incident.closed_at.is_not(None),
            func.split_part(Incident.fingerprint, ":", 1) == cause,
            func.split_part(Incident.fingerprint, ":", 2) == domain,
        )
        .order_by((Incident.fingerprint == incident.fingerprint).desc(), Incident.opened_at.desc())
        .limit(limit)
    ).all()
    if not candidates:
        return []
    ids = [c.id for c in candidates]
    # An incident could in principle be measured more than once; keep the latest. `.desc()`
    # plus `setdefault` means the first one seen per incident is the newest.
    latest_outcome: dict[uuid.UUID, Outcome] = {}
    for outcome in session.scalars(
        select(Outcome).where(Outcome.incident_id.in_(ids)).order_by(Outcome.measured_at.desc())
    ):
        latest_outcome.setdefault(outcome.incident_id, outcome)

    result = []
    for c in candidates:
        o = latest_outcome.get(c.id)
        result.append(PastIncident(
            incident_id=c.id, fingerprint=c.fingerprint, severity=c.severity.value,
            opened_at=c.opened_at, closed_at=c.closed_at,
            resolved=o.resolved if o else None,
            mttr_sec=o.mttr_sec if o else None,
            regression=o.regression if o else None,
        ))
    return result


def _indent(text: str, prefix: str = "  ") -> str:
    return "\n".join(prefix + line for line in text.splitlines())


@dataclass(frozen=True)
class Context:
    incident_id: uuid.UUID
    query_text: str
    runbooks: list[RunbookMatch]
    history: list[PastIncident]
    changes: RecentChanges | None  # None: not checked (the default; see module docstring)

    def render(self) -> str:
        lines = [f"INCIDENT: {self.query_text}", "", "RUNBOOKS:"]
        if self.runbooks:
            for m in self.runbooks:
                lines.append(f"- {m.path} — {m.heading} (score {m.score:.2f})")
                lines.append(_indent(m.content))
        else:
            lines.append("  (no runbook section scored above the relevance cutoff)")

        lines += ["", "SIMILAR PAST INCIDENTS:"]
        if self.history:
            for h in self.history:
                verdict = (
                    "resolved" if h.resolved is True
                    else "did not resolve" if h.resolved is False
                    else "outcome not yet measured"
                )
                mttr = f", MTTR {h.mttr_sec}s" if h.mttr_sec is not None else ""
                regressed = " (regressed)" if h.regression else ""
                lines.append(f"- {h.fingerprint} opened {h.opened_at:%Y-%m-%d}: "
                             f"{verdict}{mttr}{regressed}")
        else:
            lines.append("  (none — first time this kind of failure has been seen)")

        lines += ["", "RECENT CHANGES:"]
        if self.changes is None:
            lines.append("  (not checked)")
        elif not self.changes.available:
            note = "unmapped workload" if self.changes.service is None else "unavailable here"
            lines.append(f"  ({note})")
        elif not self.changes.changes:
            lines.append(f"  (none to services/{self.changes.service}/ recently)")
        else:
            lines += [f"- {c.sha} {c.at:%Y-%m-%d} {c.summary}" for c in self.changes.changes]
        return "\n".join(lines)


def build_context(
    session: Session, incident: Incident, *,
    embed_fn: Callable[[str], list[float]] = embed_one,
    changes: RecentChanges | None = None,
    runbook_limit: int = DEFAULT_RUNBOOK_LIMIT, history_limit: int = DEFAULT_HISTORY_LIMIT,
) -> Context:
    text = query_text_for(incident, incident.signals)
    return Context(
        incident_id=incident.id, query_text=text,
        runbooks=matching_runbooks(session, embed_fn(text), limit=runbook_limit),
        history=similar_incidents(session, incident, limit=history_limit),
        changes=changes,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m kaval_agent.context",
        description="Print the context that would be built for one incident (for inspection "
                     "— the diagnosis step that actually calls the model lands later in "
                     "Phase 2). Run from the laptop with `make dev-tunnel` up.",
    )
    parser.add_argument("incident_id")
    parser.add_argument("--with-changes", action="store_true",
                        help="also look up recent git commits for the incident's workload "
                             "(laptop-only; see kaval_agent.recent_changes)")
    args = parser.parse_args(argv)

    from kaval_shared.db import get_engine  # only a real run needs the database

    try:
        incident_id = uuid.UUID(args.incident_id)
    except ValueError:
        sys.exit(f"not a valid incident id: {args.incident_id!r}")

    with Session(get_engine()) as session:
        incident = session.get(Incident, incident_id)
        if incident is None:
            sys.exit(f"no incident {incident_id}")
        changes = None
        if args.with_changes:
            from kaval_agent import recent_changes as rc
            _cause, _domain, subject = incident.fingerprint.split(":", 2)
            changes = rc.for_workload(subject.rsplit("/", 1)[-1])
        print(build_context(session, incident, changes=changes).render())
    return 0


if __name__ == "__main__":
    sys.exit(main())
