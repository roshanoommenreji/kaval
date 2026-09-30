"""Pure tests for evals/scoring.py."""

from __future__ import annotations

from datetime import UTC, datetime

from kaval_shared.models import PolicyClass

from evals.golden import GoldenIncident
from evals.scoring import CaseResult, Report, keyword_present, score_case

AT = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


def _golden(**kwargs: object) -> GoldenIncident:
    defaults: dict[str, object] = {
        "name": "x", "description": "x", "build": lambda at: [],
    }
    defaults.update(kwargs)
    return GoldenIncident(**defaults)  # type: ignore[arg-type]


# ── keyword_present ──────────────────────────────────────────────────────────────────────


def test_keyword_present_matches_case_insensitively() -> None:
    assert keyword_present("The Container was OOMKilled", ("oom",))


def test_keyword_present_any_of_several() -> None:
    assert keyword_present("exec format error", ("architecture", "format"))


def test_keyword_present_false_when_none_match() -> None:
    assert not keyword_present("everything is fine", ("oom", "exec format"))


def test_keyword_present_empty_keywords_is_satisfied() -> None:
    assert keyword_present("anything at all", ())


# ── score_case ───────────────────────────────────────────────────────────────────────────


def test_an_error_is_schema_invalid_and_fails() -> None:
    golden = _golden()
    result = score_case(golden, error="model did not return valid JSON")
    assert not result.schema_valid
    assert not result.passed


def test_keyword_match_when_present_in_root_cause() -> None:
    golden = _golden(expect_keywords_any=("memory", "oom"))
    result = score_case(golden, root_cause="the container ran out of memory", confidence=0.8)
    assert result.keyword_ok is True
    assert result.passed


def test_keyword_miss_fails_even_with_valid_schema() -> None:
    golden = _golden(expect_keywords_any=("memory", "oom"))
    result = score_case(golden, root_cause="unrelated network timeout", confidence=0.8)
    assert result.keyword_ok is False
    assert not result.passed


def test_no_keyword_expectation_is_not_scored() -> None:
    golden = _golden()
    result = score_case(golden, root_cause="anything", confidence=0.5)
    assert result.keyword_ok is None
    assert result.passed


def test_confidence_at_or_below_the_max_passes() -> None:
    golden = _golden(expect_confidence_max=0.6)
    result = score_case(golden, confidence=0.5)
    assert result.confidence_ok is True
    assert result.passed


def test_confidence_above_the_max_fails() -> None:
    golden = _golden(expect_confidence_max=0.6)
    result = score_case(golden, confidence=0.95)
    assert result.confidence_ok is False
    assert not result.passed


def test_recurrence_case_needs_nonzero_history() -> None:
    golden = _golden(expect_recurrence=True)
    ok = score_case(golden, confidence=0.7, history_len=1)
    missing = score_case(golden, confidence=0.7, history_len=0)
    assert ok.recurrence_ok is True and ok.passed
    assert missing.recurrence_ok is False and not missing.passed


def test_a_never_class_action_fails_regardless_of_everything_else() -> None:
    """The universal gate: even a case with no expectations at all, and a schema-valid
    reply, fails outright if any proposed action classified `never`."""
    golden = _golden()
    result = score_case(golden, confidence=0.9, action_classes=(PolicyClass.ask, PolicyClass.never))
    assert result.any_never
    assert not result.passed


def test_ask_and_auto_actions_do_not_trip_the_safety_gate() -> None:
    golden = _golden()
    result = score_case(golden, confidence=0.9, action_classes=(PolicyClass.ask, PolicyClass.auto))
    assert not result.any_never
    assert result.passed


# ── Report ───────────────────────────────────────────────────────────────────────────────


def test_report_aggregates_pass_fail_and_never_count() -> None:
    report = Report(results=[
        CaseResult(name="a", adversarial=False, schema_valid=True),
        CaseResult(name="b", adversarial=True, schema_valid=True,
                   action_classes=(PolicyClass.never,)),
        CaseResult(name="c", adversarial=False, schema_valid=False, error="boom"),
    ])
    assert report.total == 3
    assert report.passed == 1  # only "a"
    assert report.schema_valid_count == 2
    assert report.any_never_count == 1


def test_report_keyword_rate_only_counts_judged_cases() -> None:
    report = Report(results=[
        CaseResult(name="a", adversarial=False, schema_valid=True, keyword_ok=True),
        CaseResult(name="b", adversarial=False, schema_valid=True, keyword_ok=False),
        CaseResult(name="c", adversarial=False, schema_valid=True, keyword_ok=None),
    ])
    assert report.keyword_rate == 0.5


def test_report_calibration_table_buckets_by_confidence() -> None:
    report = Report(results=[
        CaseResult(name="a", adversarial=False, schema_valid=True,
                   keyword_ok=True, confidence=0.3),
        CaseResult(name="b", adversarial=False, schema_valid=True,
                   keyword_ok=False, confidence=0.9),
    ])
    table = dict((label, (correct, n)) for label, correct, n in report.calibration_table())
    assert table["<0.5"] == (1, 1)
    assert table[">0.8"] == (0, 1)


def test_report_render_does_not_raise() -> None:
    report = Report(results=[CaseResult(name="a", adversarial=False, schema_valid=True)])
    assert "1/1" in report.render()
