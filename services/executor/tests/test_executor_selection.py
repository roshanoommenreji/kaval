"""What `kaval_executor.executor` selects, refuses and records — the defense-in-depth that
makes a compromised agent (or gateway) harmless (ADR-0006 rule 3, KAV-47).

`_refusal()` is pure and takes `fresh: PolicyClass` as a plain argument, so the tests that
exercise it, and `execute_one` via a monkeypatched `policy_mod.classify`, never need a real
`opa` binary: `services/agent/tests/test_policy.py` already proves `classify()` itself is
correct against the real `policy/` bundle. What this file proves is independent of that —
that the executor acts correctly on whatever `classify()` returns, including when it
disagrees with an action's stored `policy_class`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from kaval_agent import policy as policy_mod
from kaval_executor import executor, k8s
from kaval_shared.models import (
    Action,
    BlastRadius,
    Decision,
    Execution,
    ExecutionStatus,
    Incident,
    PolicyClass,
    Proposal,
    RiskLevel,
    Severity,
    Verdict,
)
from sqlalchemy.orm import Session

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _action(
    db: Session, *, action_type: str = "restart_pod", target: str | None = None,
    policy_class: PolicyClass = PolicyClass.ask, verdict: Verdict | None = None,
    confidence: float = 0.95, with_execution: bool = False,
) -> Action:
    incident = Incident(fingerprint=f"test-{uuid.uuid4()}", severity=Severity.high,
                        opened_at=NOW)
    proposal = Proposal(incident=incident, summary="restart checkout",
                        root_cause="oom", confidence=confidence, risk=RiskLevel.low,
                        model="gemma3:1b-it-qat", tokens_in=1, tokens_out=1,
                        cost_usd=Decimal("0"))
    action = Action(
        proposal=proposal, type=action_type, target=target or f"kaval-demo/{uuid.uuid4().hex[:8]}",
        params={}, reversible=True, blast_radius=BlastRadius.pod, policy_class=policy_class,
    )
    db.add_all([incident, proposal, action])
    db.flush()
    if verdict is not None:
        db.add(Decision(action_id=action.id, verdict=verdict, actor="test", decided_at=NOW))
        db.flush()
    if with_execution:
        db.add(Execution(action_id=action.id, status=ExecutionStatus.success,
                         started_at=NOW, finished_at=NOW))
        db.flush()
    db.refresh(action)
    return action


# ── _refusal: pure, no opa, no database ─────────────────────────────────────────────────

def _bare(policy_class: PolicyClass, decision: Decision | None) -> Action:
    action = Action(type="restart_pod", target="kaval-demo/checkout", params={},
                    reversible=True, blast_radius=BlastRadius.pod, policy_class=policy_class)
    action.decision = decision
    return action


def test_refusal_none_when_fresh_check_says_auto() -> None:
    assert executor._refusal(_bare(PolicyClass.auto, None), PolicyClass.auto) is None


def test_refusal_set_when_fresh_check_says_never_even_if_stored_auto() -> None:
    reason = executor._refusal(_bare(PolicyClass.auto, None), PolicyClass.never)
    assert reason is not None and "never" in reason


def test_refusal_none_when_fresh_says_ask_but_a_human_approved() -> None:
    decision = Decision(verdict=Verdict.approved, actor="roshan", decided_at=NOW)
    assert executor._refusal(_bare(PolicyClass.ask, decision), PolicyClass.ask) is None


def test_refusal_none_when_fresh_says_ask_and_verdict_is_auto_approved() -> None:
    decision = Decision(verdict=Verdict.auto_approved, actor="policy-engine", decided_at=NOW)
    assert executor._refusal(_bare(PolicyClass.auto, decision), PolicyClass.ask) is None


def test_refusal_set_when_fresh_says_ask_and_a_human_denied() -> None:
    decision = Decision(verdict=Verdict.denied, actor="roshan", decided_at=NOW)
    reason = executor._refusal(_bare(PolicyClass.ask, decision), PolicyClass.ask)
    assert reason is not None and "no human approval" in reason


def test_refusal_set_when_stored_auto_but_fresh_check_now_disagrees_and_says_ask() -> None:
    # The drift case: promotions.json changed (or the stored class was simply wrong) between
    # proposal time and now. No decision exists, because the action was never meant to need
    # one — which is exactly why this must refuse rather than fall through to acting.
    reason = executor._refusal(_bare(PolicyClass.auto, None), PolicyClass.ask)
    assert reason is not None and "ask" in reason


# ── execute_one, with policy_mod.classify monkeypatched: deterministic, no opa needed ──────

def test_execute_one_runs_restart_pod_on_a_missing_target_and_records_success(
    db_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A target in the expected namespace/name shape but pointing at nothing real: k8s.py's
    # own 404 handling (proven in its own module) means this exercises execute_one's happy
    # path without needing a live cluster — restart_pod() is monkeypatched here instead,
    # since this test's job is execute_one's bookkeeping, not k8s.py's API calls.
    monkeypatch.setattr(policy_mod, "classify", lambda *a, **kw: PolicyClass.auto)
    monkeypatch.setattr(
        k8s, "SUPPORTED_ACTIONS",
        {"restart_pod": lambda target: ({"found": True}, {"deleted": True}, f"deleted {target}")},
    )
    action = _action(db_session, policy_class=PolicyClass.auto)
    result = executor.execute_one(db_session, action)
    assert result.status == ExecutionStatus.success
    db_session.refresh(action)
    assert action.execution is not None and action.execution.status == ExecutionStatus.success
    assert action.execution.before_state == {"found": True}


def test_execute_one_refuses_and_records_a_never_on_fresh_reclassification(
    db_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(policy_mod, "classify", lambda *a, **kw: PolicyClass.never)
    action = _action(db_session, policy_class=PolicyClass.auto)  # stored class wrongly auto
    result = executor.execute_one(db_session, action)
    assert result.status == ExecutionStatus.skipped
    db_session.refresh(action)
    assert action.execution is not None
    assert "never" in (action.execution.stdout or "")
    assert "refused" in (action.execution.stdout or "")


def test_execute_one_never_calls_a_handler_when_refused(
    db_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def _spy(target: str) -> tuple[dict[str, object], dict[str, object], str]:
        nonlocal called
        called = True
        return {}, {}, ""

    monkeypatch.setattr(policy_mod, "classify", lambda *a, **kw: PolicyClass.never)
    monkeypatch.setattr(k8s, "SUPPORTED_ACTIONS", {"restart_pod": _spy})
    executor.execute_one(db_session, _action(db_session, policy_class=PolicyClass.auto))
    assert called is False


def test_execute_one_refuses_an_unsupported_action_type(
    db_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(policy_mod, "classify", lambda *a, **kw: PolicyClass.ask)
    action = _action(db_session, action_type="scale_deployment", policy_class=PolicyClass.ask,
                     verdict=Verdict.approved)
    result = executor.execute_one(db_session, action)
    assert result.status == ExecutionStatus.skipped
    db_session.refresh(action)
    assert action.execution is not None
    assert "no executor handler" in (action.execution.stdout or "")


def test_execute_one_redacts_stdout_before_writing_it(
    db_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "role arn:aws:iam::000000000000:role/kaval-executor"
    monkeypatch.setattr(policy_mod, "classify", lambda *a, **kw: PolicyClass.auto)
    monkeypatch.setattr(k8s, "SUPPORTED_ACTIONS",
                        {"restart_pod": lambda target: ({}, {}, secret)})
    action = _action(db_session, policy_class=PolicyClass.auto)
    executor.execute_one(db_session, action)
    db_session.refresh(action)
    assert action.execution is not None
    assert "000000000000" not in (action.execution.stdout or "")
    assert "arn:aws:iam" not in (action.execution.stdout or "")


def test_execute_one_records_a_failure_without_raising(
    db_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(target: str) -> tuple[dict[str, object], dict[str, object], str]:
        raise RuntimeError("cluster unreachable")

    monkeypatch.setattr(policy_mod, "classify", lambda *a, **kw: PolicyClass.auto)
    monkeypatch.setattr(k8s, "SUPPORTED_ACTIONS", {"restart_pod": _boom})
    action = _action(db_session, policy_class=PolicyClass.auto)
    result = executor.execute_one(db_session, action)  # must not raise
    assert result.status == ExecutionStatus.failed
    db_session.refresh(action)
    assert action.execution is not None
    assert "cluster unreachable" in (action.execution.stdout or "")


# ── _ready_actions and run_once: real selection against a real database ────────────────────

def test_ready_actions_selects_auto_and_human_approved_only(db_session: Session) -> None:
    target = f"kaval-demo/{uuid.uuid4().hex[:8]}"
    auto = _action(db_session, target=target, policy_class=PolicyClass.auto)
    approved = _action(db_session, target=target, policy_class=PolicyClass.ask,
                       verdict=Verdict.approved)
    _action(db_session, target=target, policy_class=PolicyClass.ask)  # no decision yet
    _action(db_session, target=target, policy_class=PolicyClass.ask, verdict=Verdict.denied)
    ready_ids = {a.id for a in executor._ready_actions(db_session)
                if a.target == target}
    assert ready_ids == {auto.id, approved.id}


def test_ready_actions_excludes_already_executed(db_session: Session) -> None:
    target = f"kaval-demo/{uuid.uuid4().hex[:8]}"
    _action(db_session, target=target, policy_class=PolicyClass.auto, with_execution=True)
    assert all(a.target != target for a in executor._ready_actions(db_session))


def test_run_once_is_idempotent(
    db_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(policy_mod, "classify", lambda *a, **kw: PolicyClass.auto)
    monkeypatch.setattr(k8s, "SUPPORTED_ACTIONS",
                        {"restart_pod": lambda target: ({}, {}, "ok")})
    target = f"kaval-demo/{uuid.uuid4().hex[:8]}"
    _action(db_session, target=target, policy_class=PolicyClass.auto)
    first = [r for r in executor.run_once(db_session) if r.action_type == "restart_pod"]
    second = executor.run_once(db_session)
    assert len(first) >= 1
    assert all(r.action_type != "restart_pod" or r.action_id not in {f.action_id for f in first}
              for r in second)
