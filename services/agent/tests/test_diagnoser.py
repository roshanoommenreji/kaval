"""The diagnosis worker (KAV-87, ADR-0055): which incident it picks, that it never diagnoses one
twice, what it does when the model fails, and the speed limit. The model is a fake function; the
database is real Postgres (these tests skip, not fail, when none is reachable)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from kaval_agent import diagnoser as d
from kaval_agent.context import Context
from kaval_agent.diagnose import DiagnosisError, ModelUsage
from kaval_agent.schema import Diagnosis
from kaval_shared.models import Incident, Proposal, Severity
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
MODEL = "m"
OOM = "oom_killed:k8s:kaval-demo/checkout"
SETTINGS = d.Settings(max_age=timedelta(hours=4), max_per_hour=6)


def _incident(db: Session, fingerprint: str = OOM, *, age: timedelta = timedelta(minutes=10),
              closed: bool = False, at: datetime = NOW) -> Incident:
    incident = Incident(
        id=uuid.uuid4(), fingerprint=fingerprint, severity=Severity.high,
        opened_at=at - age, closed_at=at if closed else None,
    )
    db.add(incident)
    db.commit()
    return incident


def _context(_session: Session, incident: Incident) -> Context:
    return Context(
        incident_id=incident.id, query_text="x", runbooks=[], history=[], changes=None,
        fix_target="kaval-demo/checkout", fix_cause="oom_killed",
    )


def _diagnosis(actions: bool = True) -> Diagnosis:
    body: dict[str, object] = {
        "summary": "s", "root_cause": "r", "confidence": 0.8, "risk": "low", "actions": [],
    }
    if actions:
        body["actions"] = [{
            "type": "increase_memory_limit", "target": "kaval-demo/checkout", "params": {},
            "reversible": True, "blast_radius": "deployment",
        }]
    return Diagnosis.model_validate(body)


class FakeModel:
    """Stands in for `diagnose.diagnose`; records what it was asked about."""

    def __init__(self, *, actions: bool = True, fail: Exception | None = None) -> None:
        self.calls: list[Context] = []
        self.actions, self.fail = actions, fail

    def __call__(self, context: Context) -> tuple[Diagnosis, ModelUsage]:
        self.calls.append(context)
        if self.fail is not None:
            raise self.fail
        return _diagnosis(self.actions), ModelUsage(tokens_in=10, tokens_out=5)


def _pass(db: Session, model: FakeModel, *, backoff: d.Backoff | None = None,
          settings: d.Settings = SETTINGS, now: datetime = NOW) -> d.PassResult | None:
    return d.diagnose_one_pass(
        db, backoff=backoff or d.Backoff(), settings=settings, model=MODEL, now=now,
        diagnose_fn=model, context_fn=_context,
    )


def _proposals(db: Session, incident: Incident) -> int:
    return int(db.scalar(select(func.count()).select_from(Proposal)
                         .where(Proposal.incident_id == incident.id)) or 0)


# ── which incident is picked ────────────────────────────────────────────────────────────────


def test_a_new_incident_gets_a_proposal_without_anyone_asking(db_session: Session) -> None:
    incident = _incident(db_session)
    model = FakeModel()
    result = _pass(db_session, model)
    assert result is not None and result.outcome == d.OK
    assert result.incident_id == incident.id
    assert _proposals(db_session, incident) == 1
    assert [c.incident_id for c in model.calls] == [incident.id]


def test_the_oldest_waiting_incident_goes_first(db_session: Session) -> None:
    newer = _incident(db_session, "oom_killed:k8s:kaval-demo/a", age=timedelta(minutes=5))
    older = _incident(db_session, "oom_killed:k8s:kaval-demo/b", age=timedelta(minutes=30))
    result = _pass(db_session, FakeModel())
    assert result is not None and result.incident_id == older.id
    assert _proposals(db_session, newer) == 0


def test_an_incident_is_never_diagnosed_twice(db_session: Session) -> None:
    incident = _incident(db_session)
    model = FakeModel()
    assert _pass(db_session, model) is not None
    again = _pass(db_session, model)
    assert again is not None and again.outcome == d.IDLE
    assert len(model.calls) == 1
    assert _proposals(db_session, incident) == 1


def test_a_proposal_with_no_action_still_counts_as_diagnosed(db_session: Session) -> None:
    incident = _incident(db_session, "cost_spike:aws:ec2/eval-r31")
    model = FakeModel(actions=False)
    result = _pass(db_session, model)
    assert result is not None and result.outcome == d.OK_NO_FIX
    assert _pass(db_session, model) is not None and len(model.calls) == 1
    assert _proposals(db_session, incident) == 1


@pytest.mark.parametrize("fingerprint", [
    "kaval_loop_stalled:k8s:kaval-staging/executor",
    "kaval_diagnosis_backlog:k8s:kaval-staging/diagnoser",
    "disk_pressure:k8s:kaval-staging/node",
    "latency_high:k8s:kaval-staging/gateway",
])
def test_an_incident_for_a_person_is_never_sent_to_the_model(
    db_session: Session, fingerprint: str,
) -> None:
    incident = _incident(db_session, fingerprint)
    model = FakeModel()
    result = _pass(db_session, model)
    assert result is not None and result.outcome == d.IDLE
    assert model.calls == [] and _proposals(db_session, incident) == 0


def test_a_closed_incident_is_left_alone(db_session: Session) -> None:
    incident = _incident(db_session, closed=True)
    model = FakeModel()
    result = _pass(db_session, model)
    assert result is not None and result.outcome == d.IDLE
    assert model.calls == [] and _proposals(db_session, incident) == 0


def test_an_incident_older_than_the_limit_is_left_for_a_person(db_session: Session) -> None:
    incident = _incident(db_session, age=timedelta(hours=5))
    model = FakeModel()
    result = _pass(db_session, model)
    assert result is not None and result.outcome == d.IDLE
    assert model.calls == [] and _proposals(db_session, incident) == 0


# ── when the model fails ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("error", [
    DiagnosisError("did not return a valid diagnosis"),
    httpx.ConnectError("connection refused"),
    httpx.ReadTimeout("too slow"),
])
def test_a_model_failure_writes_nothing_and_leaves_the_incident_waiting(
    db_session: Session, error: Exception,
) -> None:
    incident = _incident(db_session)
    backoff = d.Backoff()
    result = _pass(db_session, FakeModel(fail=error), backoff=backoff)
    assert result is not None and result.outcome == d.MODEL_ERROR
    assert result.waiting == 1
    assert _proposals(db_session, incident) == 0
    assert backoff.failures == 1 and backoff.until is not None
    # still waiting, so the next pass after the pause tries it again
    retry = _pass(db_session, FakeModel(), backoff=backoff, now=NOW + timedelta(minutes=1))
    assert retry is not None and retry.outcome == d.OK
    assert backoff.failures == 0 and backoff.until is None


def test_during_the_pause_the_model_is_not_asked(db_session: Session) -> None:
    _incident(db_session)
    backoff = d.Backoff()
    _pass(db_session, FakeModel(fail=DiagnosisError("x")), backoff=backoff)
    model = FakeModel()
    result = _pass(db_session, model, backoff=backoff, now=NOW + timedelta(seconds=5))
    assert result is not None and result.outcome == d.BACKING_OFF
    assert model.calls == []


def test_a_bug_in_our_own_code_is_not_hidden_as_a_model_failure(db_session: Session) -> None:
    _incident(db_session)
    with pytest.raises(ZeroDivisionError):
        _pass(db_session, FakeModel(fail=ZeroDivisionError()))


# ── the speed limit and the lock ────────────────────────────────────────────────────────────


def test_over_the_hourly_limit_incidents_wait_and_the_model_is_not_asked(
    db_session: Session,
) -> None:
    # The proposal's time is stamped by the database's clock, so this test uses the real now.
    now = datetime.now(UTC)
    first = _incident(db_session, "oom_killed:k8s:kaval-demo/a", age=timedelta(minutes=40), at=now)
    _incident(db_session, "oom_killed:k8s:kaval-demo/b", age=timedelta(minutes=20), at=now)
    limit = d.Settings(max_age=timedelta(hours=4), max_per_hour=1)
    model = FakeModel()
    done = _pass(db_session, model, settings=limit, now=now)
    assert done is not None and done.incident_id == first.id
    held = _pass(db_session, model, settings=limit, now=now)
    assert held is not None and held.outcome == d.RATE_LIMITED and held.waiting == 1
    assert len(model.calls) == 1


def test_a_second_copy_does_nothing_while_the_first_holds_the_lock(
    db_session: Session, migrated_engine: Engine,
) -> None:
    _incident(db_session)
    model = FakeModel()
    with migrated_engine.connect() as other:
        other.execute(text("SELECT pg_advisory_lock(:k)"), {"k": d.LOCK_KEY})
        try:
            assert _pass(db_session, model) is None
        finally:
            other.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": d.LOCK_KEY})
    assert model.calls == []


def test_a_limited_confidence_is_counted_and_said_in_the_log_line(db_session: Session) -> None:
    """KAV-88: an incident with no signals behind it is the thinnest there is (the model said 0.8,
    the proposal keeps 0.5). The worker counts it and says so; it does not hide it."""
    incident = _incident(db_session)
    before = d._LIMITED._value.get()
    result = _pass(db_session, FakeModel())
    assert result is not None and result.outcome == d.OK
    assert d._LIMITED._value.get() == before + 1
    assert "confidence 0.50 (the model said 0.80; thin evidence)" in result.detail
    stored = db_session.scalars(select(Proposal).where(Proposal.incident_id == incident.id)).one()
    assert stored.confidence == 0.5


def test_a_confidence_that_was_not_limited_is_not_counted(db_session: Session) -> None:
    """The negative case: a cost incident is not about a workload, so its number stands."""
    _incident(db_session, "cost_spike:aws:ec2/eval-r32")
    before = d._LIMITED._value.get()
    result = _pass(db_session, FakeModel(actions=False))
    assert result is not None and "thin evidence" not in result.detail
    assert d._LIMITED._value.get() == before


# ── small pieces that need no database ──────────────────────────────────────────────────────


def test_the_pause_doubles_after_each_failure_and_stops_at_ten_minutes() -> None:
    backoff = d.Backoff()
    waits = []
    for _ in range(8):
        backoff.failed(NOW)
        assert backoff.until is not None
        waits.append((backoff.until - NOW).total_seconds())
    assert waits[:4] == [30, 60, 120, 240]
    assert max(waits) == 600
    backoff.worked()
    assert not backoff.waiting(NOW)


def test_settings_fall_back_to_the_defaults_rather_than_switching_a_limit_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DIAGNOSE_MAX_PER_HOUR", "0")
    monkeypatch.setenv("DIAGNOSE_MAX_AGE_HOURS", "soon")
    settings = d.Settings.from_env()
    assert settings.max_per_hour == d.DEFAULT_MAX_PER_HOUR
    assert settings.max_age == timedelta(hours=d.DEFAULT_MAX_AGE_HOURS)
    monkeypatch.setenv("DIAGNOSE_MAX_PER_HOUR", "2")
    assert d.Settings.from_env().max_per_hour == 2


def test_outcomes_are_counted_but_only_by_a_fixed_label() -> None:
    before = d._DIAGNOSES.labels(d.OK)._value.get()
    lines = d.record(d.PassResult(d.OK, uuid.uuid4(), 0, 1.5, "proposal x"))
    assert d._DIAGNOSES.labels(d.OK)._value.get() == before + 1
    assert lines and lines[0].startswith(d.OK)
    assert d.record(None) == ["another diagnoser holds the lock; skipped this pass"]
    assert d.record(d.PassResult(d.IDLE)) == ["nothing waiting"]


def test_the_outcome_label_only_ever_takes_a_fixed_handful_of_words() -> None:
    """The metrics page is readable by every pod, so a label may never carry data (an id, a
    target, an error). `outcome` is safe only because `record` is the one place it is set and it
    is handed a constant."""
    allowed = {d.OK, d.OK_NO_FIX, d.MODEL_ERROR, d.RATE_LIMITED}
    for outcome in (d.OK, d.OK_NO_FIX, d.MODEL_ERROR, d.RATE_LIMITED, d.IDLE, d.BACKING_OFF):
        d.record(d.PassResult(outcome, uuid.uuid4(), 1, 0.5, "detail with an id and a target"))
    seen = {sample.labels["outcome"] for metric in d._DIAGNOSES.collect()
            for sample in metric.samples}
    assert seen <= allowed


def test_the_runbook_folder_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAVAL_RUNBOOKS_DIR", "/somewhere")
    assert str(d.runbooks_dir()).replace("\\", "/") == "/somewhere"
