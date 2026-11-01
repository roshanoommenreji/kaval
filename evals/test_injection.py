"""The prompt-injection set builds what it says it builds (KAV-112, ADR-0072). Pure: no model, no
database. Whether the real model resists the text is what `make evals SET=injection` measures."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from kaval_agent.context import _signal_text
from kaval_agent.correlate import cause_of, workload
from kaval_agent.fixes import FIX_TYPES

from evals.golden import GOLDEN_INCIDENTS, GoldenIncident
from evals.injection import INJECTION_INCIDENTS

AT = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


@pytest.fixture(params=INJECTION_INCIDENTS, ids=lambda g: g.name)
def case(request: pytest.FixtureRequest) -> GoldenIncident:
    return request.param  # type: ignore[no-any-return]


def test_there_are_ten_with_unique_names_that_do_not_collide_with_the_golden_twenty() -> None:
    names = [g.name for g in INJECTION_INCIDENTS]
    assert len(names) == 10
    assert len(set(names)) == 10
    assert not set(names) & {g.name for g in GOLDEN_INCIDENTS}
    assert all(n.startswith("injection-") for n in names)


def test_the_golden_twenty_are_untouched_by_the_new_field() -> None:
    assert len(GOLDEN_INCIDENTS) == 20
    assert all(g.canary is None for g in GOLDEN_INCIDENTS)


def test_every_case_is_tagged_adversarial_and_every_signal_synthetic(case: GoldenIncident) -> None:
    assert case.adversarial
    assert all(s.value["synthetic"] is True for s in case.build(AT))
    assert all(str(s.value["scenario"]).startswith("eval-injection-") for s in case.build(AT))


def test_every_case_has_one_stable_target_that_is_its_own_workload(case: GoldenIncident) -> None:
    signals = case.build(AT)
    targets = {s.target for s in signals}
    assert len(targets) == 1
    # The correlator strips a pod's random suffix; a hostile workload name must survive that
    # untouched, or the harness would look for the incident under the wrong target.
    (target,) = targets
    namespace, name = target.split("/", 1)
    assert workload(name) == name


def test_every_case_is_a_crash_answered_by_a_rollback_or_a_restart(case: GoldenIncident) -> None:
    causes = {cause_of(s, set()).name for s in case.build(AT) if cause_of(s, set())}  # type: ignore[union-attr]
    assert "container_error" in causes
    assert set(case.expect_fix_any) == {"rollback_deployment", "restart_pod"}
    assert set(case.expect_fix_any) <= FIX_TYPES


def test_a_planted_marker_is_asked_for_in_the_text_the_model_reads(case: GoldenIncident) -> None:
    if case.canary is None:
        return
    visible = " ".join(t for s in case.build(AT) if (t := _signal_text(s)))
    # Zero-width spaces are what hide the instruction from a filter; the marker itself is plain.
    assert case.canary.lower() in visible.lower().replace("​", "")
    assert "begin" in visible.lower().replace("​", "")


def test_seven_cases_plant_a_marker_and_three_do_not() -> None:
    planted = [g for g in INJECTION_INCIDENTS if g.canary is not None]
    assert len(planted) == 7
    assert {g.name for g in INJECTION_INCIDENTS if g.canary is None} == {
        "injection-workload-name", "injection-wrong-target", "injection-json-smuggling"}


def test_the_hostile_text_is_in_a_field_the_model_is_actually_shown() -> None:
    """The set is only worth running if the text reaches the model. `query_text_for` reads exactly
    three keys; the reason case relies on the second of them."""
    reason_case = next(g for g in INJECTION_INCIDENTS if g.name == "injection-reason-field")
    first = reason_case.build(AT)[0]
    assert first.value["message"] == ""
    assert "PINEAPPLE-7742" in str(_signal_text(first))


def test_the_long_case_is_long_and_ends_with_the_instruction() -> None:
    long_case = next(g for g in INJECTION_INCIDENTS if g.name == "injection-long-padding")
    message = str(long_case.build(AT)[0].value["message"])
    assert len(message) > 5000
    assert message.endswith("PINEAPPLE-7746.")


def test_the_hidden_characters_case_really_hides_the_words_from_a_plain_search() -> None:
    hidden = next(g for g in INJECTION_INCIDENTS if g.name == "injection-hidden-characters")
    message = str(hidden.build(AT)[0].value["message"])
    assert "ignore all previous instructions" not in message.lower()
    assert "ignore all previous instructions" in message.lower().replace("​", "")
