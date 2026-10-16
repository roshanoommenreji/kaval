"""kaval_agent.evidence: the stored confidence can never be higher than the evidence allows
(KAV-88, ADR-0056). Pure tests need no database; the last ones go through the one place every
proposal is written."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from kaval_agent import diagnose as dg
from kaval_agent import evidence
from kaval_agent.schema import Diagnosis
from kaval_shared.models import Incident, Proposal, Severity, Signal
from sqlalchemy.orm import Session

AT = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
WORKLOAD = "oom_killed:k8s:kaval-demo/checkout"


def _signal(kind: str, n: int = 0, *, target: str = "kaval-demo/checkout") -> Signal:
    return Signal(
        id=uuid.uuid4(), source="kubernetes", kind=kind, target=target, value={"n": n},
        observed_at=AT + timedelta(seconds=n),
    )


def _model_says(confidence: float) -> Diagnosis:
    return Diagnosis.model_validate({
        "summary": "checkout keeps OOMing", "root_cause": "memory limit too low",
        "confidence": confidence, "risk": "medium", "actions": [],
    })


# ── the rule ────────────────────────────────────────────────────────────────────────────


def test_a_single_observation_caps_a_confident_model_at_half() -> None:
    result = evidence.limit(0.95, WORKLOAD, [_signal("container_memory_near_limit")])
    assert result.confidence == evidence.ONE_OBSERVATION_CAP == 0.5
    assert result.stated == 0.95
    assert result.limited is True
    assert "one observation" in result.reason


def test_several_observations_of_one_kind_cap_at_seven_tenths() -> None:
    """Three 'restarting' events and never why: more than a hint, still not the cause."""
    signals = [_signal("pod_back_off", n) for n in range(3)]
    result = evidence.limit(0.9, WORKLOAD, signals)
    assert result.confidence == evidence.ONE_KIND_CAP == 0.7
    assert result.limited is True


def test_a_real_memory_incident_holds_only_the_kill_and_keeps_the_models_number() -> None:
    """Seen live on staging 2026-10-16: 17 `pod_oom_killed` and nothing else. The kill is the cause
    itself, so one kind is not thin evidence here."""
    signals = [_signal("pod_oom_killed", n) for n in range(17)]
    result = evidence.limit(0.95, WORKLOAD, signals)
    assert result.confidence == 0.95
    assert result.limited is False


def test_one_kill_alone_is_still_only_a_hint() -> None:
    assert evidence.limit(0.95, WORKLOAD, [_signal("pod_oom_killed")]).confidence == 0.5


def test_the_kill_does_not_vouch_for_a_different_cause() -> None:
    """The exemption is per cause: restarts under a crash-loop name are still 'never why'."""
    signals = [_signal("pod_back_off", n) for n in range(5)]
    assert evidence.limit(0.9, "crashloop:k8s:kaval-demo/checkout", signals).confidence == 0.7
    kills = [_signal("pod_oom_killed", n) for n in range(5)]
    assert evidence.limit(0.9, "crashloop:k8s:kaval-demo/checkout", kills).confidence == 0.7


def test_the_exec_format_exit_is_direct_evidence_for_its_own_cause() -> None:
    exits = [_signal("container_exited", n) for n in range(3)]
    assert evidence.limit(0.9, "exec_format:k8s:kaval-demo/gateway", exits).confidence == 0.9
    assert evidence.limit(0.9, "exec_format:k8s:kaval-demo/gateway", exits[:1]).confidence == 0.5


def test_two_kinds_of_evidence_are_left_alone() -> None:
    """The negative case: a well-evidenced incident keeps exactly what the model said."""
    signals = [_signal("pod_oom_killed"), _signal("pod_back_off", 1), _signal("pod_back_off", 2)]
    result = evidence.limit(0.95, WORKLOAD, signals)
    assert result.confidence == 0.95
    assert result.limited is False
    assert result.reason == ""


def test_a_number_below_the_cap_is_never_raised() -> None:
    result = evidence.limit(0.2, WORKLOAD, [_signal("container_memory_near_limit")])
    assert result.confidence == 0.2
    assert result.limited is False  # nothing was changed


def test_an_incident_with_no_signals_is_capped_like_a_single_observation() -> None:
    assert evidence.limit(0.9, WORKLOAD, []).confidence == 0.5


@pytest.mark.parametrize("fingerprint", [
    "daily_cost_spike:aws:ec2/eval-x",           # a cost spike is one measurement by nature
    "volume_unattached:aws:ebs/vol-0abc",        # so is an unused disk
])
def test_incidents_that_are_not_about_a_workload_are_not_capped(fingerprint: str) -> None:
    result = evidence.limit(0.9, fingerprint, [_signal("volume_unattached", target="ebs/x")])
    assert result.confidence == 0.9
    assert result.limited is False


def test_the_same_observation_counted_twice_is_still_one_kind() -> None:
    same = [_signal("pod_back_off", 0), _signal("pod_back_off", 0)]
    assert evidence.limit(0.9, WORKLOAD, same).confidence == evidence.ONE_KIND_CAP


# ── the one place every proposal is written ─────────────────────────────────────────────


def _incident(db_session: Session, signals: list[Signal]) -> Incident:
    incident = Incident(
        id=uuid.uuid4(), fingerprint=WORKLOAD, severity=Severity.high, opened_at=AT,
    )
    db_session.add(incident)
    db_session.add_all(signals)
    db_session.flush()
    incident.signals.extend(signals)
    db_session.flush()
    return incident


def test_write_proposal_stores_the_limited_number(db_session: Session) -> None:
    incident = _incident(db_session, [_signal("container_memory_near_limit")])
    proposal = dg.write_proposal(db_session, incident, _model_says(0.95),
                                 dg.ModelUsage(tokens_in=1, tokens_out=1), model="m")
    assert db_session.get(Proposal, proposal.id).confidence == 0.5  # type: ignore[union-attr]


def test_write_proposal_keeps_the_models_number_when_the_evidence_is_there(
    db_session: Session,
) -> None:
    incident = _incident(db_session, [_signal("pod_oom_killed"), _signal("pod_back_off", 1)])
    proposal = dg.write_proposal(db_session, incident, _model_says(0.95),
                                 dg.ModelUsage(tokens_in=1, tokens_out=1), model="m")
    assert db_session.get(Proposal, proposal.id).confidence == 0.95  # type: ignore[union-attr]
