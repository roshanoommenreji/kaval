"""The reply format narrowed to one incident (KAV-84, ADR-0054): the schema sent to the model, the
second check on what comes back, and the retry when a reply breaks the rule."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from kaval_agent import diagnose as dg
from kaval_agent import fixes
from kaval_agent.context import Context
from kaval_agent.schema import Diagnosis

TARGET = "kaval-demo/checkout-r11"


def _context(cause: str | None, target: str | None = TARGET) -> Context:
    return Context(
        incident_id=uuid.uuid4(), query_text="x", runbooks=[], history=[], changes=None,
        fix_target=target, fix_cause=cause,
    )


def _reply(actions: list[dict[str, object]]) -> httpx.Response:
    body = {"summary": "s", "root_cause": "r", "confidence": 0.8, "risk": "low",
            "actions": actions}
    return httpx.Response(200, json={"message": {"content": json.dumps(body)},
                                     "prompt_eval_count": 10, "eval_count": 5})


def _action(type_: str = "increase_memory_limit", target: str = TARGET) -> dict[str, object]:
    return {"type": type_, "target": target, "params": {}, "reversible": True,
            "blast_radius": "deployment"}


def _parse(actions: list[dict[str, object]]) -> Diagnosis:
    return Diagnosis.model_validate({"summary": "s", "root_cause": "r", "confidence": 0.8,
                                     "risk": "low", "actions": actions})


# ── narrow_schema ───────────────────────────────────────────────────────────────────────────


def test_a_cause_with_a_fix_allows_exactly_one_action_of_that_type_aimed_at_the_workload() -> None:
    schema = fixes.narrow_schema(Diagnosis.model_json_schema(), "oom_killed", TARGET)
    assert schema["properties"]["actions"]["minItems"] == 1
    assert schema["properties"]["actions"]["maxItems"] == 1
    props = schema["$defs"]["DiagnosisAction"]["properties"]
    assert props["type"]["enum"] == ["increase_memory_limit"]
    assert props["target"]["enum"] == [TARGET]


def test_a_cause_with_two_fixes_allows_either() -> None:
    schema = fixes.narrow_schema(Diagnosis.model_json_schema(), "crashloop", TARGET)
    enum = schema["$defs"]["DiagnosisAction"]["properties"]["type"]["enum"]
    assert set(enum) == {"rollback_deployment", "restart_pod"}


@pytest.mark.parametrize("cause", ["cost_spike", "idle_volume", "disk_pressure", "latency_high",
                                    "kaval_loop_stalled", "something_new"])
def test_a_cause_with_no_fix_allows_no_action_at_all(cause: str) -> None:
    schema = fixes.narrow_schema(Diagnosis.model_json_schema(), cause, None)
    assert schema["properties"]["actions"]["maxItems"] == 0


def test_an_unknown_cause_leaves_the_schema_alone() -> None:
    schema = Diagnosis.model_json_schema()
    assert fixes.narrow_schema(schema, None, TARGET) == schema


def test_narrowing_never_changes_the_shared_schema() -> None:
    schema = Diagnosis.model_json_schema()
    before = json.dumps(schema, sort_keys=True)
    fixes.narrow_schema(schema, "oom_killed", TARGET)
    assert json.dumps(schema, sort_keys=True) == before
    assert "enum" not in Diagnosis.model_json_schema()["$defs"]["DiagnosisAction"]["properties"][
        "type"]


# ── violations: the same rules, checked on the parsed reply ─────────────────────────────────


def test_the_right_fix_aimed_at_the_workload_is_fine() -> None:
    assert fixes.violations(_parse([_action()]), "oom_killed", TARGET) is None


def test_no_action_for_a_cause_with_a_fix_is_a_violation() -> None:
    assert "exactly one" in (fixes.violations(_parse([]), "oom_killed", TARGET) or "")


def test_two_actions_are_a_violation() -> None:
    reply = _parse([_action(), _action("restart_pod")])
    assert "exactly one" in (fixes.violations(reply, "oom_killed", TARGET) or "")


def test_a_fix_that_does_not_answer_the_cause_is_a_violation() -> None:
    assert "must be one of" in (
        fixes.violations(_parse([_action("restart_pod")]), "oom_killed", TARGET) or "")


def test_an_invented_type_is_a_violation() -> None:
    assert fixes.violations(_parse([_action("delete_everything")]), "oom_killed", TARGET)


def test_a_wrong_target_is_a_violation() -> None:
    reply = _parse([_action(target="kaval-demo/other")])
    assert "target must be exactly" in (fixes.violations(reply, "oom_killed", TARGET) or "")


def test_any_action_for_a_cause_with_no_fix_is_a_violation() -> None:
    assert fixes.violations(_parse([_action()]), "cost_spike", None)
    assert fixes.violations(_parse([]), "cost_spike", None) is None


def test_with_no_known_cause_nothing_is_checked() -> None:
    assert fixes.violations(_parse([_action("anything")]), None, None) is None


# ── diagnose(): what is sent, and the retry ─────────────────────────────────────────────────


def test_diagnose_sends_the_narrowed_schema_for_this_incident() -> None:
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.read()))
        return _reply([_action()])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    dg.diagnose(_context("oom_killed"), model="m", client=client)
    fmt = sent[0]["format"]
    assert fmt["properties"]["actions"]["maxItems"] == 1
    assert fmt["$defs"]["DiagnosisAction"]["properties"]["target"]["enum"] == [TARGET]
    assert "exactly one action" in sent[0]["messages"][1]["content"]


def test_a_reply_that_breaks_the_rule_gets_one_corrective_retry() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read())
        calls.append(payload)
        if len(calls) == 1:
            return _reply([_action("restart_pod")])
        assert "broke a rule" in payload["messages"][-1]["content"]
        return _reply([_action()])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result, _usage = dg.diagnose(_context("oom_killed"), model="m", client=client)
    assert len(calls) == 2
    assert result.actions[0].type == "increase_memory_limit"


def test_two_replies_that_break_the_rule_raise_and_name_the_rule() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _reply([_action("EC2 Instance Termination")])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(dg.DiagnosisError, match="must be one of"):
        dg.diagnose(_context("oom_killed"), model="m", client=client)


def test_the_baseline_switch_sends_the_plain_schema_and_checks_nothing() -> None:
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.read()))
        return _reply([_action("anything_at_all")])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result, _ = dg.diagnose(_context("oom_killed"), model="m", client=client,
                            narrow_to_incident=False)
    assert "enum" not in sent[0]["format"]["$defs"]["DiagnosisAction"]["properties"]["type"]
    assert result.actions[0].type == "anything_at_all"
