"""Every lab shows up on its phase's row of the dashboard without anyone typing it in (KAV-83)."""

from __future__ import annotations

import dashboard  # type: ignore[import-not-found]  # same folder; pytest puts it on the path

REPO = "https://github.com/example/kaval"


def lab(num: int, phase: str) -> dict:
    return {"id": f"Lab {num:02d}", "phase": phase, "file": f"lab-{num:02d}-x.md"}


def test_a_lab_lands_on_the_phase_in_its_header() -> None:
    labs = [lab(44, "5,"), lab(45, "5,"), lab(43, "3"), lab(19, "4")]
    assert [c["label"] for c in dashboard.lab_chips(labs, 5, set(), REPO)] == ["Lab 44", "Lab 45"]
    assert [c["label"] for c in dashboard.lab_chips(labs, 3, set(), REPO)] == ["Lab 43"]


def test_a_lab_with_a_hand_written_link_is_not_listed_twice() -> None:
    chips = dashboard.lab_chips([lab(8, "2"), lab(9, "2")], 2, {"Lab 08"}, REPO)
    assert [c["label"] for c in chips] == ["Lab 09"]


def test_the_link_points_at_the_lab_file_in_the_repo() -> None:
    (chip,) = dashboard.lab_chips([lab(50, "5,")], 5, set(), REPO)
    assert chip["url"] == f"{REPO}/blob/main/docs/labs/lab-50-x.md"


def test_a_lab_with_no_readable_phase_is_left_off_rather_than_put_on_the_wrong_row() -> None:
    assert dashboard.lab_chips([lab(1, "?")], 0, set(), REPO) == []


def test_every_real_lab_is_on_some_phase_row() -> None:
    labs = dashboard.read_labs(dashboard.ROOT / "docs" / "labs")
    shown = {c["label"] for n in range(10) for c in dashboard.lab_chips(labs, n, set(), REPO)}
    assert {lb["id"] for lb in labs} == shown


def test_paused_is_explained_as_a_cost_posture_not_a_status() -> None:
    assert "cost" in dashboard.POSTURE_TIPS["paused"]
    assert set(dashboard.POSTURE_TIPS) >= {"local", "dev-server", "paused", "always-on"}
