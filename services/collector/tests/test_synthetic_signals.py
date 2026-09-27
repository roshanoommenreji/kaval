"""The synthetic source: real row shapes, always flagged as fake, reproducible by seed.

No __init__.py here, like services/gateway/tests: a second package named `tests` would
collide with services/shared/tests under pytest's default import mode.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from kaval_collector import __version__, synthetic
from kaval_shared.models import Signal
from sqlalchemy import select
from sqlalchemy.orm import Session

AT = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def _shape(signals: list[Signal]) -> list[tuple[object, ...]]:
    """Everything the seed controls: all of it except the per-run id."""
    return [
        (s.source, s.kind, s.target, {k: v for k, v in s.value.items() if k != "run_id"},
         s.observed_at)
        for s in signals
    ]


@pytest.mark.parametrize("scenario", sorted(synthetic.SCENARIOS))
def test_every_signal_is_flagged_fake_and_grouped_by_run(scenario: str) -> None:
    signals = synthetic.generate(scenario, at=AT, seed=1)
    assert signals
    run_ids = {s.value["run_id"] for s in signals}
    assert len(run_ids) == 1
    for s in signals:
        assert s.value["synthetic"] is True
        assert s.value["scenario"] == scenario
        json.dumps(s.value)  # must survive the trip into JSONB


@pytest.mark.parametrize("scenario", sorted(synthetic.SCENARIOS))
def test_oldest_first_and_never_in_the_future(scenario: str) -> None:
    times = [s.observed_at for s in synthetic.generate(scenario, at=AT, seed=1)]
    assert times == sorted(times)
    assert times[-1] <= AT


def test_same_seed_same_signals_but_a_new_run() -> None:
    a = synthetic.generate("oom-crashloop", at=AT, seed=42)
    b = synthetic.generate("oom-crashloop", at=AT, seed=42)
    assert _shape(a) == _shape(b)
    assert a[0].value["run_id"] != b[0].value["run_id"]


def test_different_seeds_are_different_pods() -> None:
    a = synthetic.generate("oom-crashloop", at=AT, seed=1)
    b = synthetic.generate("oom-crashloop", at=AT, seed=2)
    assert a[0].target != b[0].target
    assert a[0].value["run_id"] != b[0].value["run_id"]


def test_unknown_scenario_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown scenario"):
        synthetic.generate("disk-full", at=AT)


def test_oom_crashloop_reads_like_the_real_failure() -> None:
    signals = synthetic.generate("oom-crashloop", at=AT, seed=1)
    kinds = [s.kind for s in signals]
    assert kinds[0] == "container_memory_near_limit"  # the warning comes first
    assert "pod_oom_killed" in kinds
    assert kinds[-1] == "alert_firing"
    restarts = [r for s in signals if s.kind == "pod_back_off"
                and isinstance(r := s.value["restart_count"], int)]
    assert restarts == sorted(restarts) and len(restarts) >= 2
    assert {s.target for s in signals} == {signals[0].target}  # one pod, one incident


def test_exec_format_carries_the_arm64_clue() -> None:
    signals = synthetic.generate("exec-format", at=AT, seed=1)
    assert any("exec format error" in str(s.value.get("message")) for s in signals)


def test_cost_spike_is_money_as_strings_and_actually_a_spike() -> None:
    days = synthetic.generate("cost-spike", at=AT, seed=1)
    amounts = [Decimal(str(s.value["amount_usd"])) for s in days]
    baseline, spike = amounts[:-1], amounts[-1]
    assert all(isinstance(s.value["amount_usd"], str) for s in days)
    assert spike > 5 * (sum(baseline) / len(baseline))


def test_dry_run_prints_json_and_never_touches_the_database(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_db() -> None:
        raise AssertionError("dry run opened the database")

    monkeypatch.setattr("kaval_shared.db.get_engine", no_db)
    assert synthetic.main(["idle-volume", "--seed", "3", "--dry-run"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert [json.loads(line)["kind"] for line in lines] == ["volume_unattached"]


def test_list_names_every_scenario(capsys: pytest.CaptureFixture[str]) -> None:
    assert synthetic.main(["--list"]) == 0
    out = capsys.readouterr().out
    assert all(name in out for name in synthetic.SCENARIOS)



def test_version_flag_reports_the_component_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_:
        synthetic.main(["--version"])
    assert exit_.value.code == 0
    assert capsys.readouterr().out.strip() == f"kaval-collector {__version__}"

def test_a_run_lands_whole_in_the_database(db_session: Session) -> None:
    signals = synthetic.generate("oom-crashloop", seed=7)
    run_id = signals[0].value["run_id"]
    assert synthetic.emit(db_session, signals) == len(signals)

    stored = db_session.scalars(
        select(Signal).where(Signal.value.contains({"run_id": run_id}))
    ).all()
    assert len(stored) == len(signals)
    assert all(s.created_at is not None for s in stored)
