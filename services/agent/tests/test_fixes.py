"""The list of fixes the model is told about (KAV-84, ADR-0054) against the list the executor can
actually carry out, and where the model reads the list."""

from __future__ import annotations

import uuid

from kaval_agent import diagnose as dg
from kaval_agent import fixes
from kaval_agent.context import Context, fix_target_of
from kaval_agent.schema import Diagnosis
from kaval_executor import k8s


def _context(target: str | None, cause: str | None = None) -> Context:
    return Context(
        incident_id=uuid.uuid4(), query_text="oom killed", runbooks=[], history=[], changes=None,
        fix_target=target, fix_cause=cause,
    )


def test_the_model_is_told_exactly_the_fixes_the_executor_can_carry_out() -> None:
    """If an action is added to the executor and not here, the model never proposes it; if one is
    named here and not in the executor, it is proposed and then skipped. Either is a defect, so
    CI fails on it rather than someone remembering."""
    assert fixes.FIX_TYPES == set(k8s.SUPPORTED_ACTIONS)


def test_every_fix_is_described_and_has_a_blast_radius_the_schema_accepts() -> None:
    allowed = {"pod", "deployment", "namespace", "node", "account"}
    for fix in fixes.FIXES:
        assert fix.does and fix.use_when and fix.not_when
        assert fix.blast_radius in allowed
    assert len({f.type for f in fixes.FIXES}) == len(fixes.FIXES)


def test_no_fix_is_one_the_policy_would_refuse_by_its_name() -> None:
    """The model is never taught an action whose type policy refuses on sight."""
    banned = ("pvc", "persistentvolumeclaim", "namespace", "iam", "ec2", "billing")
    for fix in fixes.FIXES:
        assert not any(word in fix.type.lower() for word in banned), fix.type


def test_the_message_ends_with_the_fix_for_this_cause() -> None:
    """The fix is the LAST thing the model reads: placed in the system prompt it was followed in
    1 of 5 tries, at the end of the message in 5 of 5 (Lab 51)."""
    message = dg.user_message(_context("kaval-demo/checkout-r11", "oom_killed"))
    tail = message.split("FIXES YOU MAY PROPOSE", 1)[1]
    assert "increase_memory_limit" in tail
    assert "exactly one action" in tail
    assert message.rstrip().endswith("params stays empty.")


def test_with_no_known_cause_the_model_may_still_say_none_fits() -> None:
    tail = dg.user_message(_context("kaval-demo/x", None)).split("FIXES YOU MAY PROPOSE", 1)[1]
    assert "empty actions list" in tail


def test_only_the_fix_that_answers_the_cause_is_shown() -> None:
    oom = dg.user_message(_context("kaval-demo/x", "oom_killed"))
    assert "increase_memory_limit" in oom
    assert "rollback_deployment" not in oom and "restart_pod" not in oom
    fmt = dg.user_message(_context("kaval-demo/x", "exec_format"))
    assert "rollback_deployment" in fmt
    assert "increase_memory_limit" not in fmt and "restart_pod" not in fmt


def test_a_cause_with_no_fix_is_told_so_and_shown_no_action_to_copy() -> None:
    for cause in ("cost_spike", "idle_volume", "disk_pressure", "latency_high",
                  "kaval_loop_stalled", "kube_node_not_ready"):
        message = dg.user_message(_context(None, cause))
        tail = message.split("NO FIX AVAILABLE", 1)[1]
        assert "empty actions list" in tail
        for fix in fixes.FIXES:
            assert fix.type not in message, (cause, fix.type)


def test_an_unknown_cause_shows_every_fix() -> None:
    message = dg.user_message(_context("kaval-demo/x", None))
    for fix in fixes.FIXES:
        assert fix.type in message


def test_every_fix_answers_at_least_one_cause_and_none_a_cause_for_a_person() -> None:
    from kaval_shared import health

    for fix in fixes.FIXES:
        assert fix.causes
        for cause in fix.causes:
            assert not health.is_people_only_cause(cause), (fix.type, cause)
            assert not health.is_health_cause(cause), (fix.type, cause)


def test_every_cause_a_fix_answers_is_one_the_correlator_can_name() -> None:
    """A typo here would silently make a fix unreachable."""
    import inspect

    from kaval_agent import correlate

    source = inspect.getsource(correlate.cause_of)
    for fix in fixes.FIXES:
        for cause in fix.causes:
            assert f'"{cause}"' in source, (fix.type, cause)


def test_the_target_is_handed_over_exactly_not_left_to_the_model() -> None:
    message = dg.user_message(_context("kaval-demo/checkout-r11"))
    assert "the target is exactly: kaval-demo/checkout-r11" in message


def test_an_incident_with_no_workload_gets_no_invented_target() -> None:
    message = dg.user_message(_context(None))
    assert "target is exactly" not in message
    assert "named in the incident" in message


def test_the_system_prompt_keeps_the_original_instructions_and_points_at_the_list() -> None:
    assert dg.DEFAULT_SYSTEM_PROMPT.startswith(dg.BASE_SYSTEM_PROMPT)
    assert "list of fixes" in dg.DEFAULT_SYSTEM_PROMPT


def test_the_prompt_does_not_teach_an_action_the_executor_lacks() -> None:
    assert "scale_deployment" not in dg.DEFAULT_SYSTEM_PROMPT
    assert "scale_deployment" not in dg.user_message(_context("kaval-demo/x"))


def test_only_a_kubernetes_workload_gets_a_fix_target() -> None:
    assert fix_target_of("oom_killed:k8s:kaval-demo/checkout") == "kaval-demo/checkout"
    assert fix_target_of("cost_spike:aws:ec2/eval-r31") is None
    assert fix_target_of("volume_unattached:aws:ebs/vol-1") is None
    assert fix_target_of("crashloop:k8s:no-namespace") is None


def test_the_escalation_path_reads_the_same_message() -> None:
    import inspect

    from kaval_agent import escalate

    assert "dg.user_message(context)" in inspect.getsource(escalate)


def test_actions_are_required_in_the_schema_the_model_decodes_against() -> None:
    """Optional, the 1B model left it out every time and never proposed anything."""
    assert "actions" in Diagnosis.model_json_schema()["required"]


def test_the_schema_describes_action_type_with_the_real_names_only() -> None:
    schema = Diagnosis.model_json_schema()
    description = schema["$defs"]["DiagnosisAction"]["properties"]["type"]["description"]
    for fix in fixes.FIXES:
        assert fix.type in description
    assert "scale_deployment" not in description


def test_the_type_stays_free_text_so_policy_can_still_see_what_the_model_wrote() -> None:
    """A made-up type is still accepted by the schema (and then refused by policy or skipped by
    the executor), not silently turned into one of the three. See ADR-0054."""
    action = {"type": "EC2 Instance Termination", "target": "a/b", "reversible": True,
              "blast_radius": "node"}
    diagnosis = Diagnosis.model_validate({
        "summary": "s", "root_cause": "r", "confidence": 0.5, "risk": "low", "actions": [action],
    })
    assert diagnosis.actions[0].type == "EC2 Instance Termination"
