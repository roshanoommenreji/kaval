"""Context assembly: query text, rendering (pure), and retrieval against real Postgres (KAV-40).

No __init__.py here, like the other services' tests.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from kaval_agent import context as ctx
from kaval_agent.recent_changes import Change, RecentChanges
from kaval_shared.models import Incident, Outcome, RunbookChunk, Severity, Signal
from sqlalchemy import delete
from sqlalchemy.orm import Session

AT = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def _clear_runbook_chunks(db_session: Session) -> None:
    """`matching_runbooks` ranks across the whole table. A shared dev database may already
    hold real, `kaval_agent.index_runbooks`-written rows (Lab 10); cleared inside
    `db_session`'s own rolled-back transaction, so nothing real is actually touched."""
    db_session.execute(delete(RunbookChunk))
    db_session.flush()


def _signal(kind: str, **value: object) -> Signal:
    return Signal(id=uuid.uuid4(), source="kubernetes", kind=kind,
                  target="kaval-demo/checkout", value=dict(value), observed_at=AT)


def _incident(fingerprint: str, **kw: object) -> Incident:
    kw.setdefault("opened_at", AT)
    return Incident(id=uuid.uuid4(), fingerprint=fingerprint, severity=Severity.high, **kw)


# ── query_text_for (pure) ─────────────────────────────────────────────────────────────────


def test_query_text_starts_with_the_fingerprint_in_words() -> None:
    incident = _incident("oom_killed:k8s:kaval-demo/checkout")
    text = ctx.query_text_for(incident, [])
    assert text.startswith("oom killed on kaval-demo/checkout (k8s)")


def test_query_text_adds_distinct_signal_messages() -> None:
    incident = _incident("oom_killed:k8s:kaval-demo/checkout")
    signals = [
        _signal("pod_oom_killed", reason="OOMKilled", message="killed for memory"),
        _signal("pod_back_off", reason="BackOff"),
        _signal("pod_back_off", reason="BackOff"),  # a duplicate message: not repeated
    ]
    text = ctx.query_text_for(incident, signals)
    assert text.count("BackOff") == 1
    assert "killed for memory" in text  # message preferred over reason when both present


def test_query_text_caps_at_four_distinct_messages() -> None:
    incident = _incident("x:k8s:kaval-demo/checkout")
    signals = [_signal("k", reason=f"r{i}") for i in range(10)]
    text = ctx.query_text_for(incident, signals)
    assert sum(f"r{i}" in text for i in range(10)) == 4


# ── Context.render() (pure) ───────────────────────────────────────────────────────────────


def test_render_shows_no_match_honestly() -> None:
    rendered = ctx.Context(
        incident_id=uuid.uuid4(), query_text="oom on checkout", runbooks=[], history=[],
        changes=None,
    ).render()
    assert "no runbook section scored above" in rendered
    assert "first time this kind of failure" in rendered
    assert "(not checked)" in rendered  # changes=None


def test_render_shows_a_runbook_match() -> None:
    match = ctx.RunbookMatch(path="restore-from-backup.md", heading="Likely causes",
                             content="Database lost or corrupted — Likely causes\n\n1. ...",
                             score=0.82)
    rendered = ctx.Context(incident_id=uuid.uuid4(), query_text="q", runbooks=[match],
                           history=[], changes=None).render()
    assert "restore-from-backup.md — Likely causes (score 0.82)" in rendered
    assert "1. ..." in rendered


def test_render_shows_history_with_and_without_an_outcome() -> None:
    resolved = ctx.PastIncident(
        incident_id=uuid.uuid4(), fingerprint="oom_killed:k8s:kaval-demo/checkout",
        severity="high", opened_at=AT, closed_at=AT, resolved=True, mttr_sec=90,
        regression=False,
    )
    unmeasured = ctx.PastIncident(
        incident_id=uuid.uuid4(), fingerprint="oom_killed:k8s:kaval-demo/checkout",
        severity="high", opened_at=AT, closed_at=AT, resolved=None, mttr_sec=None,
        regression=None,
    )
    rendered = ctx.Context(incident_id=uuid.uuid4(), query_text="q", runbooks=[],
                           history=[resolved, unmeasured], changes=None).render()
    assert "resolved, MTTR 90s" in rendered
    assert "outcome not yet measured" in rendered


def test_render_shows_recent_changes_or_says_why_not() -> None:
    unmapped = ctx.Context(incident_id=uuid.uuid4(), query_text="q", runbooks=[], history=[],
                           changes=RecentChanges(service=None, available=False, changes=[]))
    assert "unmapped workload" in unmapped.render()

    unavailable = ctx.Context(incident_id=uuid.uuid4(), query_text="q", runbooks=[], history=[],
                              changes=RecentChanges(service="gateway", available=False,
                                                    changes=[]))
    assert "unavailable here" in unavailable.render()

    empty = ctx.Context(incident_id=uuid.uuid4(), query_text="q", runbooks=[], history=[],
                        changes=RecentChanges(service="gateway", available=True, changes=[]))
    assert "none to services/gateway/" in empty.render()

    found = ctx.Context(
        incident_id=uuid.uuid4(), query_text="q", runbooks=[], history=[],
        changes=RecentChanges(service="gateway", available=True,
                              changes=[Change(sha="abc123def4", at=AT, summary="fix: a bug")]),
    )
    assert "abc123def4" in found.render() and "fix: a bug" in found.render()


# ── Against real Postgres (skipped without one; CI requires it) ──────────────────────────


def _onehot(i: int) -> list[float]:
    from kaval_shared.models import RUNBOOK_EMBED_DIM
    v = [0.0] * RUNBOOK_EMBED_DIM
    v[i] = 1.0
    return v


def test_matching_runbooks_ranks_by_similarity_and_drops_the_unrelated(
    db_session: Session,
) -> None:
    _clear_runbook_chunks(db_session)
    near = RunbookChunk(path="a.md", heading="Near", ordinal=0, content="near",
                        content_hash="h1", embedding=_onehot(0))
    partial = RunbookChunk(path="a.md", heading="Partial", ordinal=1, content="partial",
                           content_hash="h2", embedding=[0.8, 0.6] + [0.0] * 382)
    far = RunbookChunk(path="a.md", heading="Far", ordinal=2, content="far",
                       content_hash="h3", embedding=_onehot(1))
    db_session.add_all([near, partial, far])
    db_session.flush()

    matches = ctx.matching_runbooks(db_session, _onehot(0), limit=5)
    assert [m.heading for m in matches] == ["Near", "Partial"]  # "Far" is below the cutoff
    assert matches[0].score == pytest.approx(1.0)
    assert matches[1].score == pytest.approx(0.8)


def test_matching_runbooks_respects_the_limit(db_session: Session) -> None:
    _clear_runbook_chunks(db_session)
    for i in range(3):
        db_session.add(RunbookChunk(path="a.md", heading=f"H{i}", ordinal=i, content="c",
                                    content_hash=f"h{i}", embedding=_onehot(0)))
    db_session.flush()
    assert len(ctx.matching_runbooks(db_session, _onehot(0), limit=2)) == 2


def test_similar_incidents_ranks_exact_fingerprint_first_excludes_open_and_other_causes(
    db_session: Session,
) -> None:
    # "testdomain" is not a domain the correlator ever produces (only "k8s" and "aws"), so
    # this can't collide with real incidents already sitting in a shared dev database.
    self_incident = _incident("kav40_test_cause:testdomain:kaval-demo/checkout", closed_at=None)
    exact_older = _incident("kav40_test_cause:testdomain:kaval-demo/checkout", closed_at=AT,
                            opened_at=AT - timedelta(days=5))
    exact_newer = _incident("kav40_test_cause:testdomain:kaval-demo/checkout", closed_at=AT,
                            opened_at=AT - timedelta(days=1))
    same_kind_diff_subject = _incident("kav40_test_cause:testdomain:kaval-demo/other",
                                       closed_at=AT, opened_at=AT - timedelta(days=2))
    different_cause = _incident("kav40_other_cause:testdomain:kaval-demo/checkout", closed_at=AT)
    still_open_same_fp = _incident("kav40_test_cause:testdomain:kaval-demo/checkout",
                                   closed_at=None)
    db_session.add_all([self_incident, exact_older, exact_newer, same_kind_diff_subject,
                        different_cause, still_open_same_fp])
    db_session.flush()
    db_session.add(Outcome(incident_id=exact_newer.id, resolved=True, mttr_sec=120,
                           regression=False, measured_at=AT))
    db_session.flush()

    history = ctx.similar_incidents(db_session, self_incident, limit=3)
    assert [h.incident_id for h in history] == [
        exact_newer.id, exact_older.id, same_kind_diff_subject.id,
    ]
    assert history[0].resolved is True and history[0].mttr_sec == 120
    assert history[1].resolved is None  # no outcome recorded for this one


def test_similar_incidents_is_empty_when_nothing_matches(db_session: Session) -> None:
    lonely = _incident("kav40_lonely_cause:testdomain:widget/only-one", closed_at=None)
    db_session.add(lonely)
    db_session.flush()
    assert ctx.similar_incidents(db_session, lonely) == []


def test_build_context_assembles_all_three_and_leaves_changes_as_given(
    db_session: Session,
) -> None:
    _clear_runbook_chunks(db_session)
    db_session.add(RunbookChunk(path="a.md", heading="H", ordinal=0,
                                content="oom killed content", content_hash="h",
                                embedding=_onehot(0)))
    incident = _incident("kav40_test_cause:testdomain:kaval-demo/checkout", closed_at=None)
    db_session.add(incident)
    db_session.flush()

    result = ctx.build_context(
        db_session, incident, embed_fn=lambda _text: _onehot(0),
        changes=RecentChanges(service="gateway", available=True, changes=[]),
    )
    assert result.incident_id == incident.id
    assert result.runbooks[0].heading == "H"
    assert result.history == []
    assert result.changes is not None and result.changes.service == "gateway"
