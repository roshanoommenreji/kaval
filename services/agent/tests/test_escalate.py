"""kaval_agent.escalate: the Bedrock escalation path against a mocked transport (never a real
Bedrock call), and the local-first orchestration decision (KAV-44)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

import anthropic
import httpx
import httpx2
import pytest
from kaval_agent import diagnose as dg
from kaval_agent import escalate as esc
from kaval_agent.context import Context, PastIncident, RunbookMatch
from kaval_agent.diagnose import DiagnosisError, ModelUsage
from kaval_agent.schema import Diagnosis
from kaval_shared.models import Incident, Proposal, Severity
from sqlalchemy.orm import Session

_A_RUNBOOK = RunbookMatch(path="docs/runbooks/oom.md", heading="OOM", content="...", score=0.7)
_A_PAST_INCIDENT = PastIncident(
    incident_id=uuid.uuid4(), fingerprint="oom:k8s:kaval-demo/checkout", severity="high",
    opened_at=datetime(2026, 9, 1, tzinfo=UTC), closed_at=datetime(2026, 9, 1, 1, tzinfo=UTC),
    resolved=True, mttr_sec=3600, regression=False,
)

VALID_DIAGNOSIS = {
    "summary": "checkout keeps OOMing",
    "root_cause": "memory limit too low for the workload's actual usage",
    "confidence": 0.85,
    "risk": "medium",
    "actions": [],
}


def _context(*, runbooks: list[RunbookMatch] | None = None,
             history: list[PastIncident] | None = None) -> Context:
    return Context(
        incident_id=uuid.uuid4(), query_text="oom killed on kaval-demo/checkout (k8s)",
        runbooks=runbooks or [], history=history or [], changes=None,
    )


def _bedrock_client(
    handler: Callable[[httpx2.Request], httpx2.Response],
) -> anthropic.AnthropicBedrock:
    # AnthropicBedrock, not AnthropicBedrockMantle — see escalate.py's module docstring for
    # why. It has no skip_auth; fake static credentials sign the request fine since the mocked
    # transport intercepts before anything reaches a real network call.
    return anthropic.AnthropicBedrock(
        aws_region="ap-south-1", aws_access_key="test", aws_secret_key="test",
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )


def _tool_use_reply(input_: object, *, tokens_in: int = 100, tokens_out: int = 40,
                     tool_use_id: str = "toolu_1") -> httpx2.Response:
    return httpx2.Response(200, json={
        "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-haiku-4-5",
        "stop_reason": "tool_use", "stop_sequence": None,
        "content": [{"type": "tool_use", "id": tool_use_id, "name": "emit_diagnosis",
                      "input": input_}],
        "usage": {"input_tokens": tokens_in, "output_tokens": tokens_out},
    })


def _text_only_reply() -> httpx2.Response:
    return httpx2.Response(200, json={
        "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-haiku-4-5",
        "stop_reason": "end_turn", "stop_sequence": None,
        "content": [{"type": "text", "text": "I can't help with that."}],
        "usage": {"input_tokens": 30, "output_tokens": 10},
    })


# ── should_escalate() ────────────────────────────────────────────────────────────────────


def test_a_local_failure_always_escalates() -> None:
    context = _context(runbooks=[_A_RUNBOOK], history=[_A_PAST_INCIDENT])
    assert esc.should_escalate(None, context) is True


def test_no_retrieved_evidence_escalates_regardless_of_confidence() -> None:
    diagnosis = Diagnosis.model_validate({**VALID_DIAGNOSIS, "confidence": 0.99})
    assert esc.should_escalate(diagnosis, _context(runbooks=[], history=[])) is True


def test_low_confidence_alone_escalates_even_with_evidence() -> None:
    diagnosis = Diagnosis.model_validate({**VALID_DIAGNOSIS, "confidence": 0.2})
    context = _context(runbooks=[_A_RUNBOOK], history=[_A_PAST_INCIDENT])
    assert esc.should_escalate(diagnosis, context) is True


def test_confident_with_evidence_does_not_escalate() -> None:
    diagnosis = Diagnosis.model_validate({**VALID_DIAGNOSIS, "confidence": 0.85})
    context = _context(runbooks=[_A_RUNBOOK], history=[_A_PAST_INCIDENT])
    assert esc.should_escalate(diagnosis, context) is False


# ── estimate_cost_usd() ───────────────────────────────────────────────────────────────────


def test_cost_is_computed_from_the_response_s_own_token_counts() -> None:
    usage = ModelUsage(tokens_in=1_000_000, tokens_out=1_000_000)
    expected = esc.HAIKU_INPUT_USD_PER_MTOK + esc.HAIKU_OUTPUT_USD_PER_MTOK
    assert esc.estimate_cost_usd(usage) == expected


def test_zero_tokens_cost_nothing() -> None:
    assert esc.estimate_cost_usd(ModelUsage(tokens_in=0, tokens_out=0)) == Decimal("0")


# ── escalate() (pure, mocked transport) ──────────────────────────────────────────────────


def test_a_valid_first_reply_needs_one_call() -> None:
    calls = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        payload = json.loads(request.content)
        calls.append(payload)
        assert payload["tool_choice"] == {"type": "tool", "name": "emit_diagnosis"}
        assert payload["tools"][0]["name"] == "emit_diagnosis"
        assert payload["tools"][0]["strict"] is True
        return _tool_use_reply(VALID_DIAGNOSIS)

    client = _bedrock_client(handler)
    result, usage = esc.escalate(
        _context(), client=client, model_id="apac.anthropic.claude-haiku-4-5"
    )
    assert len(calls) == 1
    assert result.root_cause == VALID_DIAGNOSIS["root_cause"]
    assert usage.tokens_in == 100
    assert usage.tokens_out == 40


def test_an_invalid_tool_call_gets_one_corrective_retry_via_tool_result() -> None:
    calls = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        payload = json.loads(request.content)
        calls.append(payload)
        if len(calls) == 1:
            return _tool_use_reply({"summary": "not enough fields"}, tool_use_id="toolu_1")
        # the correction must be a tool_result answering the same tool_use_id, not free text
        last = payload["messages"][-1]
        assert last["role"] == "user"
        assert last["content"][0]["type"] == "tool_result"
        assert last["content"][0]["tool_use_id"] == "toolu_1"
        assert last["content"][0]["is_error"] is True
        return _tool_use_reply(VALID_DIAGNOSIS, tool_use_id="toolu_2")

    client = _bedrock_client(handler)
    result, usage = esc.escalate(_context(), client=client, model_id="m")
    assert len(calls) == 2
    assert result.summary == VALID_DIAGNOSIS["summary"]
    assert usage.tokens_in == 200  # both attempts counted
    assert usage.tokens_out == 80


def test_two_invalid_replies_raise_and_write_nothing() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return _tool_use_reply({"nonsense": True})

    client = _bedrock_client(handler)
    with pytest.raises(esc.EscalationError, match="did not return a valid diagnosis"):
        esc.escalate(_context(), client=client, model_id="m")


def test_a_declined_or_textual_reply_is_a_hard_failure_not_a_retry() -> None:
    calls = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(1)
        return _text_only_reply()

    client = _bedrock_client(handler)
    with pytest.raises(esc.EscalationError, match="did not call emit_diagnosis"):
        esc.escalate(_context(), client=client, model_id="m")
    assert len(calls) == 1  # no retry spent on a structural refusal


def test_api_error_is_wrapped() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(429, json={"error": {"type": "rate_limit_error",
                                                       "message": "slow down"}})

    client = anthropic.AnthropicBedrock(
        aws_region="ap-south-1", aws_access_key="test", aws_secret_key="test", max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )
    with pytest.raises(esc.EscalationError, match="Bedrock call failed"):
        esc.escalate(_context(), client=client, model_id="m")


def test_no_model_given_or_in_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError("should not have called Bedrock")

    client = _bedrock_client(handler)
    with pytest.raises(esc.EscalationError, match="BEDROCK_MODEL_ID"):
        esc.escalate(_context(), client=client)


# ── diagnose_with_escalation() (orchestration) ───────────────────────────────────────────


def _ollama_reply(content: object, *, prompt_tokens: int = 50, eval_tokens: int = 20
                   ) -> httpx.Response:
    body = content if isinstance(content, str) else json.dumps(content)
    return httpx.Response(200, json={
        "message": {"content": body},
        "prompt_eval_count": prompt_tokens, "eval_count": eval_tokens,
    })


def test_a_confident_well_evidenced_local_answer_never_calls_bedrock() -> None:
    def local_handler(request: httpx.Request) -> httpx.Response:
        return _ollama_reply({**VALID_DIAGNOSIS, "confidence": 0.9})

    def bedrock_handler(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError("should not have escalated")

    result = esc.diagnose_with_escalation(
        _context(runbooks=[_A_RUNBOOK], history=[_A_PAST_INCIDENT]), local_model="m",
        local_client=httpx.Client(transport=httpx.MockTransport(local_handler)),
        bedrock_client=_bedrock_client(bedrock_handler), bedrock_model_id="b",
    )
    assert result.escalated is False
    assert result.model == "m"
    assert result.cost_usd == Decimal("0")


def test_low_confidence_escalates_and_returns_the_bedrock_answer_and_cost() -> None:
    def local_handler(request: httpx.Request) -> httpx.Response:
        return _ollama_reply({**VALID_DIAGNOSIS, "confidence": 0.1})

    def bedrock_handler(request: httpx2.Request) -> httpx2.Response:
        return _tool_use_reply({**VALID_DIAGNOSIS, "summary": "from bedrock"},
                                 tokens_in=1000, tokens_out=200)

    result = esc.diagnose_with_escalation(
        _context(runbooks=[_A_RUNBOOK], history=[_A_PAST_INCIDENT]), local_model="m",
        local_client=httpx.Client(transport=httpx.MockTransport(local_handler)),
        bedrock_client=_bedrock_client(bedrock_handler), bedrock_model_id="b",
    )
    assert result.escalated is True
    assert result.diagnosis.summary == "from bedrock"
    assert result.model == "b"
    assert result.cost_usd == esc.estimate_cost_usd(ModelUsage(tokens_in=1000, tokens_out=200))


def test_local_failure_escalates_to_bedrock_successfully() -> None:
    def local_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="ollama down")

    def bedrock_handler(request: httpx2.Request) -> httpx2.Response:
        return _tool_use_reply(VALID_DIAGNOSIS)

    result = esc.diagnose_with_escalation(
        _context(), local_model="m",
        local_client=httpx.Client(transport=httpx.MockTransport(local_handler)),
        bedrock_client=_bedrock_client(bedrock_handler), bedrock_model_id="b",
    )
    assert result.escalated is True
    assert result.diagnosis.summary == VALID_DIAGNOSIS["summary"]


def test_bedrock_failure_falls_back_to_the_low_confidence_local_answer() -> None:
    def local_handler(request: httpx.Request) -> httpx.Response:
        return _ollama_reply({**VALID_DIAGNOSIS, "confidence": 0.1})

    def bedrock_handler(request: httpx2.Request) -> httpx2.Response:
        return _text_only_reply()

    result = esc.diagnose_with_escalation(
        _context(), local_model="m",
        local_client=httpx.Client(transport=httpx.MockTransport(local_handler)),
        bedrock_client=_bedrock_client(bedrock_handler), bedrock_model_id="b",
    )
    assert result.escalated is False  # a worse answer beats none
    assert result.model == "m"
    assert result.cost_usd == Decimal("0")


def test_both_local_and_bedrock_failing_raises() -> None:
    def local_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="ollama down")

    def bedrock_handler(request: httpx2.Request) -> httpx2.Response:
        return _text_only_reply()

    with pytest.raises(DiagnosisError, match="local model failed"):
        esc.diagnose_with_escalation(
            _context(), local_model="m",
            local_client=httpx.Client(transport=httpx.MockTransport(local_handler)),
            bedrock_client=_bedrock_client(bedrock_handler), bedrock_model_id="b",
        )


# ── write_proposal() records a real Bedrock cost (against real Postgres) ────────────────


def test_write_proposal_records_a_nonzero_bedrock_cost(db_session: Session) -> None:
    incident = Incident(
        id=uuid.uuid4(), fingerprint="kav44_test_cause:testdomain:kaval-demo/checkout",
        severity=Severity.high, opened_at=datetime(2026, 9, 30, 12, 0, tzinfo=UTC),
    )
    db_session.add(incident)
    db_session.flush()

    diagnosis = Diagnosis.model_validate(VALID_DIAGNOSIS)
    usage = ModelUsage(tokens_in=2500, tokens_out=500)
    cost = esc.estimate_cost_usd(usage)

    proposal = dg.write_proposal(
        db_session, incident, diagnosis, usage,
        model="apac.anthropic.claude-haiku-4-5", cost_usd=cost,
    )

    stored = db_session.get(Proposal, proposal.id)
    assert stored is not None
    assert stored.cost_usd == cost
    assert stored.cost_usd > Decimal("0")
    assert stored.model == "apac.anthropic.claude-haiku-4-5"
