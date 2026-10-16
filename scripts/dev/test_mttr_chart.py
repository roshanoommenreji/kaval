"""The recorded game-day times add up, and the picture matches them (KAV-83)."""

from __future__ import annotations

import copy
import json

import mttr_chart  # type: ignore[import-not-found]  # same folder; pytest puts it on the path

DATA = json.loads(mttr_chart.DATA.read_text(encoding="utf-8"))


def test_recorded_times_add_up() -> None:
    assert mttr_chart.check(DATA) == []


def test_a_wrong_total_is_noticed() -> None:
    bad = copy.deepcopy(DATA)
    bad["runs"][0]["wall_s"] += 1
    assert any("wall-clock" in m for m in mttr_chart.check(bad))


def test_machine_time_leaves_out_the_persons_wait() -> None:
    bad = copy.deepcopy(DATA)
    bad["runs"][0]["machine_s"] = bad["runs"][0]["wall_s"]
    assert any("machine time" in m for m in mttr_chart.check(bad))


def test_an_alert_only_run_cannot_claim_a_restore_time() -> None:
    bad = copy.deepcopy(DATA)
    next(r for r in bad["runs"] if r["kind"] == "alert")["machine_s"] = 40
    assert any("alert-only" in m for m in mttr_chart.check(bad))


def test_the_committed_picture_is_current() -> None:
    assert mttr_chart.OUT.read_text(encoding="utf-8") == mttr_chart.draw(DATA)


def test_every_run_is_drawn_with_its_total() -> None:
    svg = mttr_chart.draw(DATA)
    for run in DATA["runs"]:
        assert run["name"] in svg
    assert "431 s" in svg and "168 s" in svg and "130 s" in svg
