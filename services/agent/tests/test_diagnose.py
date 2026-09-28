"""kaval_agent.diagnose: the model call against a mocked transport (never a real Ollama), and
persistence against real Postgres (KAV-41)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from kaval_agent import diagnose as dg
from kaval_agent.context import Context
from kaval_shared.models import Action, Incident, PolicyClass, Proposal, RiskLevel, Severity
from sqlalchemy import select
from sqlalchemy.orm import Session

AT = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)

VALID_DIAGNOSIS = {
    "summary": "checkout keeps OOMing",
    "root_cause": "memory limit too low for the workload's actual usage",
    "confidence": 0.8,
    "risk": "medium",
    "actions": [{
        "type": "raise_memory_limit", "target": "kaval-demo/checkout",
        "params": {"to_mi": 512}, "reversible": True, "blast_radius": "deployment",
    }],
}


def _context() -> Context:
    return Context(
        incident_id=uuid.uuid4(), query_text="oom killed on kaval-demo/checkout (k8s)",
        runbooks=[], history=[], changes=None,
    )


def _client(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=handler)


def _ollama_reply(content: object, *, prompt_tokens: int = 100, eval_tokens: int = 40
                   ) -> httpx.Response:
    body = content if isinstance(content, str) else json.dumps(content)
    return httpx.Response(200, json={
        "message": {"content": body},
        "prompt_eval_count": prompt_tokens, "eval_count": eval_tokens,
    })


# ── diagnose() (pure, mocked transport) ─────────────────────────────────────────────────


def test_a_valid_first_reply_needs_one_call() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read())
        calls.append(payload)
        assert payload["model"] == "gemma3:1b-it-qat"
        assert payload["format"]["title"] == "Diagnosis"
        return _ollama_reply(VALID_DIAGNOSIS)

    client = _client(httpx.MockTransport(handler))
    result, usage = dg.diagnose(_context(), model="gemma3:1b-it-qat", client=client)
    assert len(calls) == 1
    assert result.root_cause == VALID_DIAGNOSIS["root_cause"]
    assert usage.tokens_in == 100
    assert usage.tokens_out == 40


def test_an_invalid_reply_gets_one_corrective_retry() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read())
        calls.append(payload)
        if len(calls) == 1:
            return _ollama_reply({"summary": "not enough fields"})
        # the retry message with the validation error must have been added
        assert "did not match the required schema" in payload["messages"][-1]["content"]
        return _ollama_reply(VALID_DIAGNOSIS)

    client = _client(httpx.MockTransport(handler))
    result, usage = dg.diagnose(_context(), model="m", client=client)
    assert len(calls) == 2
    assert result.summary == VALID_DIAGNOSIS["summary"]
    assert usage.tokens_in == 200  # both attempts counted
    assert usage.tokens_out == 80


def test_two_invalid_replies_raise_and_write_nothing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _ollama_reply({"nonsense": True})

    client = _client(httpx.MockTransport(handler))
    with pytest.raises(dg.DiagnosisError, match="did not return a valid diagnosis"):
        dg.diagnose(_context(), model="m", client=client)


def test_malformed_json_counts_as_an_invalid_reply() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return _ollama_reply("not json at all {")
        return _ollama_reply(VALID_DIAGNOSIS)

    client = _client(httpx.MockTransport(handler))
    result, _usage = dg.diagnose(_context(), model="m", client=client)
    assert len(calls) == 2
    assert result.summary == VALID_DIAGNOSIS["summary"]


def test_http_error_is_wrapped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="model not found")

    client = _client(httpx.MockTransport(handler))
    with pytest.raises(dg.DiagnosisError, match="chat call failed"):
        dg.diagnose(_context(), model="m", client=client)


def test_malformed_response_shape_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    client = _client(httpx.MockTransport(handler))
    with pytest.raises(dg.DiagnosisError, match="no message.content"):
        dg.diagnose(_context(), model="m", client=client)


def test_no_model_given_or_in_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOCAL_MODEL", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not have called Ollama")

    client = _client(httpx.MockTransport(handler))
    with pytest.raises(dg.DiagnosisError, match="LOCAL_MODEL"):
        dg.diagnose(_context(), client=client)


# ── write_proposal() (against real Postgres) ────────────────────────────────────────────


def _incident(db_session: Session) -> Incident:
    incident = Incident(
        id=uuid.uuid4(), fingerprint="kav41_test_cause:testdomain:kaval-demo/checkout",
        severity=Severity.high, opened_at=AT,
    )
    db_session.add(incident)
    db_session.flush()
    return incident


def test_write_proposal_persists_proposal_and_actions(db_session: Session) -> None:
    from kaval_agent.schema import Diagnosis

    incident = _incident(db_session)
    diagnosis = Diagnosis.model_validate(VALID_DIAGNOSIS)
    usage = dg.ModelUsage(tokens_in=120, tokens_out=55)

    proposal = dg.write_proposal(db_session, incident, diagnosis, usage, model="gemma3:1b-it-qat")

    stored = db_session.get(Proposal, proposal.id)
    assert stored is not None
    assert stored.incident_id == incident.id
    assert stored.summary == diagnosis.summary
    assert stored.risk == RiskLevel.medium
    assert stored.tokens_in == 120
    assert stored.tokens_out == 55
    assert stored.cost_usd == Decimal("0")

    actions = db_session.scalars(select(Action).where(Action.proposal_id == proposal.id)).all()
    assert len(actions) == 1
    assert actions[0].policy_class == PolicyClass.ask
    assert actions[0].target == "kaval-demo/checkout"
    assert actions[0].params == {"to_mi": 512}


def test_write_proposal_with_no_actions_writes_only_the_proposal(db_session: Session) -> None:
    from kaval_agent.schema import Diagnosis

    incident = _incident(db_session)
    diagnosis = Diagnosis.model_validate({**VALID_DIAGNOSIS, "actions": []})
    usage = dg.ModelUsage(tokens_in=10, tokens_out=5)

    proposal = dg.write_proposal(db_session, incident, diagnosis, usage, model="m")

    actions = db_session.scalars(select(Action).where(Action.proposal_id == proposal.id)).all()
    assert actions == []
