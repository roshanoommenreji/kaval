"""kaval_agent.schema.Diagnosis: what a model reply must satisfy, pure (KAV-41)."""

from __future__ import annotations

import pytest
from kaval_agent.schema import Diagnosis
from pydantic import ValidationError


def _valid(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "summary": "checkout keeps OOMing",
        "root_cause": "memory limit too low for the workload's actual usage",
        "confidence": 0.8,
        "risk": "medium",
        "actions": [{
            "type": "raise_memory_limit", "target": "kaval-demo/checkout",
            "params": {"to_mi": 512}, "reversible": True, "blast_radius": "deployment",
        }],
    }
    base.update(overrides)
    return base


def test_a_valid_diagnosis_parses() -> None:
    d = Diagnosis.model_validate(_valid())
    assert d.confidence == 0.8
    assert d.actions[0].blast_radius == "deployment"


def test_actions_may_be_empty() -> None:
    d = Diagnosis.model_validate(_valid(actions=[]))
    assert d.actions == []


@pytest.mark.parametrize("confidence", [-0.01, 1.01, 2.0, -1.0])
def test_confidence_out_of_range_is_rejected(confidence: float) -> None:
    with pytest.raises(ValidationError):
        Diagnosis.model_validate(_valid(confidence=confidence))


def test_unknown_risk_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Diagnosis.model_validate(_valid(risk="catastrophic"))


def test_unknown_blast_radius_is_rejected() -> None:
    payload = _valid()
    payload["actions"] = [{**payload["actions"][0], "blast_radius": "galaxy"}]  # type: ignore[index]
    with pytest.raises(ValidationError):
        Diagnosis.model_validate(payload)


def test_extra_field_on_diagnosis_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Diagnosis.model_validate(_valid(guessed_field="not part of the schema"))


def test_extra_field_on_action_is_rejected() -> None:
    payload = _valid()
    payload["actions"] = [{**payload["actions"][0], "auto_approve": True}]  # type: ignore[index]
    with pytest.raises(ValidationError):
        Diagnosis.model_validate(payload)


def test_empty_summary_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Diagnosis.model_validate(_valid(summary=""))


def test_more_than_five_actions_is_rejected() -> None:
    action = _valid()["actions"][0]  # type: ignore[index]
    with pytest.raises(ValidationError):
        Diagnosis.model_validate(_valid(actions=[action] * 6))


def test_action_missing_reversible_is_rejected() -> None:
    payload = _valid()
    action = dict(payload["actions"][0])  # type: ignore[index]
    del action["reversible"]
    payload["actions"] = [action]
    with pytest.raises(ValidationError):
        Diagnosis.model_validate(payload)


def test_json_schema_has_no_policy_class_field() -> None:
    # The model is never given a place to put an opinion on its own policy classification.
    schema = Diagnosis.model_json_schema()
    action_schema = schema["$defs"]["DiagnosisAction"]["properties"]
    assert "policy_class" not in action_schema
