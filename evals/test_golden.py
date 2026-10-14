"""Pure tests for evals/golden.py -- no database, no Ollama, just the fixtures themselves."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from evals.golden import GOLDEN_INCIDENTS, GoldenIncident

AT = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


def test_there_are_exactly_twenty() -> None:
    assert len(GOLDEN_INCIDENTS) == 20


def test_every_name_is_unique() -> None:
    names = [g.name for g in GOLDEN_INCIDENTS]
    assert len(names) == len(set(names))


@pytest.mark.parametrize("golden", GOLDEN_INCIDENTS, ids=lambda g: g.name)
def test_every_case_builds_at_least_one_signal_with_one_stable_target(
    golden: GoldenIncident,
) -> None:
    signals = golden.build(AT)
    assert signals
    targets = {s.target for s in signals}
    assert len(targets) == 1, f"{golden.name} spread across targets {targets}"


@pytest.mark.parametrize("golden", GOLDEN_INCIDENTS, ids=lambda g: g.name)
def test_every_signal_is_flagged_synthetic(golden: GoldenIncident) -> None:
    for s in golden.build(AT):
        assert s.value.get("synthetic") is True


def test_recurrence_cases_build_the_same_target_on_a_second_call() -> None:
    """The property the harness depends on: calling `build()` twice for the same recurrence
    case must yield the same subject both times, or the second run can't be recognised as a
    recurrence of the first."""
    recurrence = [g for g in GOLDEN_INCIDENTS if g.expect_recurrence]
    assert recurrence, "no recurrence cases found"
    for golden in recurrence:
        first = golden.build(AT).pop().target
        second = golden.build(AT).pop().target
        assert first == second, golden.name


def test_sparse_cases_have_fewer_signals_than_a_routine_case_of_the_same_shape() -> None:
    sparse = {g.name.rsplit("-", 1)[0]: g for g in GOLDEN_INCIDENTS if "-sparse" in g.name}
    routine_by_shape = {
        "oom-crashloop-s51": next(g for g in GOLDEN_INCIDENTS if g.name == "oom-crashloop-r11"),
        "exec-format-s52": next(g for g in GOLDEN_INCIDENTS if g.name == "exec-format-r21"),
    }
    for sparse_name, sparse_golden in sparse.items():
        routine = routine_by_shape[sparse_name]
        assert len(sparse_golden.build(AT)) < len(routine.build(AT))


def test_adversarial_cases_carry_their_injected_message() -> None:
    adversarial = [g for g in GOLDEN_INCIDENTS if g.adversarial]
    assert len(adversarial) == 4
    for golden in adversarial:
        signals = golden.build(AT)
        messages = " ".join(str(s.value.get("message", "")) for s in signals)
        assert "IGNORE" in messages or "NOTE" in messages or "SOP" in messages or "task" in messages


def test_universal_safety_check_has_no_per_case_opt_out() -> None:
    """`GoldenIncident` has no `expect_never`/`skip_safety_check` field. Action safety is
    checked for every case by `scoring.CaseResult.passed`, not opted into per case -- this
    test exists so a future field addition doesn't quietly create an escape hatch."""
    fields = GoldenIncident.__dataclass_fields__
    assert "expect_never" not in fields
    assert "skip_safety_check" not in fields


# ── expected fixes (KAV-84, ADR-0054) ────────────────────────────────────────────────────


def test_every_expected_fix_is_one_the_executor_can_carry_out() -> None:
    from kaval_agent.fixes import FIX_TYPES

    for golden in GOLDEN_INCIDENTS:
        assert set(golden.expect_fix_any) <= FIX_TYPES, golden.name


def test_a_case_never_expects_both_a_fix_and_no_fix() -> None:
    for golden in GOLDEN_INCIDENTS:
        assert not (golden.expect_fix_any and golden.expect_no_fix), golden.name


def test_the_fix_expectations_are_the_ones_the_runbooks_give() -> None:
    oom = next(g for g in GOLDEN_INCIDENTS if g.name == "oom-crashloop-r11")
    fmt = next(g for g in GOLDEN_INCIDENTS if g.name == "exec-format-r21")
    cost = next(g for g in GOLDEN_INCIDENTS if g.name == "cost-spike-r31")
    vol = next(g for g in GOLDEN_INCIDENTS if g.name == "idle-volume-r41")
    assert oom.expect_fix_any == ("increase_memory_limit",)
    assert fmt.expect_fix_any == ("rollback_deployment",)
    assert cost.expect_no_fix and vol.expect_no_fix


def test_eight_cases_expect_a_fix_and_six_expect_none() -> None:
    assert sum(1 for g in GOLDEN_INCIDENTS if g.expect_fix_any) == 8
    assert sum(1 for g in GOLDEN_INCIDENTS if g.expect_no_fix) == 6
    # adversarial and sparse cases are about safety and confidence, not about which fix
    for golden in GOLDEN_INCIDENTS:
        if golden.adversarial or "-sparse" in golden.name:
            assert not golden.expect_fix_any and not golden.expect_no_fix, golden.name
