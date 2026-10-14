"""The frozen pre-KAV-84 prompt (evals/baseline.py) must stay frozen and stay comparable."""

from __future__ import annotations

from kaval_agent import diagnose as dg
from kaval_agent import fixes

from evals.baseline import BASELINE_SYSTEM_PROMPT, BaselineDiagnosis


def test_the_baseline_is_the_old_prompt_without_the_list_of_fixes() -> None:
    for fix in fixes.FIXES:
        assert fix.type not in BASELINE_SYSTEM_PROMPT
    assert "FIXES YOU MAY PROPOSE" not in BASELINE_SYSTEM_PROMPT


def test_the_current_prompt_starts_with_the_baseline_instructions() -> None:
    """If this fails, someone edited the base instructions: decide on purpose whether the baseline
    should move (add a new frozen copy), because it silently changes what a comparison means."""
    assert dg.BASE_SYSTEM_PROMPT == BASELINE_SYSTEM_PROMPT
    assert dg.DEFAULT_SYSTEM_PROMPT.startswith(BASELINE_SYSTEM_PROMPT)


def test_the_baseline_schema_has_actions_optional_and_the_old_wording() -> None:
    from kaval_agent.schema import Diagnosis

    old, new = BaselineDiagnosis.model_json_schema(), Diagnosis.model_json_schema()
    assert "actions" not in old["required"] and "actions" in new["required"]
    old_type = old["$defs"]["BaselineAction"]["properties"]["type"]["description"]
    assert "scale_deployment" in old_type
    assert "scale_deployment" not in new["$defs"]["DiagnosisAction"]["properties"]["type"][
        "description"]


def test_a_reply_without_actions_is_valid_for_the_baseline_and_not_for_today() -> None:
    import pytest
    from kaval_agent.schema import Diagnosis
    from pydantic import ValidationError

    reply = '{"summary": "s", "root_cause": "r", "confidence": 0.5, "risk": "low"}'
    assert BaselineDiagnosis.model_validate_json(reply).actions == []
    with pytest.raises(ValidationError):
        Diagnosis.model_validate_json(reply)
