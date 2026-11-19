"""A phase the roadmap marks `[-]` is retired: shown as retired, never the current phase, and its
struck-through lines are not open work (KAV-149, ADR-0097)."""

from __future__ import annotations

from pathlib import Path

import dashboard  # type: ignore[import-not-found]  # same folder; pytest puts it on the path

ROADMAP = """# Roadmap

## Phase 0 — Foundations · weeks 1–2 · `[x]`

- [x] one

**Exit gate:** done.

## Phase 1 — Harden · weeks 3–4 · `[{one}]`

- [x] two
- [{box}] three

**Exit gate:** published.

## Phase 2 — Mobile app · weeks 5–6 · `[-]`

**Retired.**

- ~~Expo app scaffold~~
- ~~Push notifications~~

**Exit gate:** met through Slack.

## Definition of Done — every story

- [ ] Code merged, CI green
- [ ] Lab doc
"""


def phases(tmp_path: Path, one: str = " ", box: str = " ") -> list[dashboard.Phase]:
    f = tmp_path / "ROADMAP.md"
    f.write_text(ROADMAP.format(one=one, box=box), encoding="utf-8")
    return dashboard.read_roadmap(f)


def test_a_retired_phase_is_read_and_called_retired(tmp_path: Path) -> None:
    last = phases(tmp_path)[-1]
    assert (last.num, last.marker, last.state) == (2, "-", "retired")


def test_its_struck_through_lines_are_not_tasks(tmp_path: Path) -> None:
    got = phases(tmp_path)
    assert (got[-1].done, got[-1].total) == (0, 0)
    assert sum(p.total for p in got) == 3


def test_checkboxes_under_a_later_heading_belong_to_no_phase(tmp_path: Path) -> None:
    assert all("Code merged" not in text for p in phases(tmp_path) for _, text in p.tasks)


def test_the_current_phase_is_never_a_retired_one(tmp_path: Path) -> None:
    assert dashboard.current_phase(phases(tmp_path, one="~")).num == 1
    assert dashboard.current_phase(phases(tmp_path, one=" ")).num == 1
    # everything else finished: the last phase that was really built, not the retired one
    assert dashboard.current_phase(phases(tmp_path, one="x", box="x")).num == 1


def test_retired_phases_are_not_counted_in_phase_n_of_m(tmp_path: Path) -> None:
    assert len(dashboard.live_phases(phases(tmp_path))) == 2


def test_the_row_says_retired_instead_of_showing_an_empty_bar(tmp_path: Path) -> None:
    got = phases(tmp_path)
    retired = dashboard.phase_tail(got[-1])
    assert "retired, not built" in retired and 'class="bar"' not in retired
    normal = dashboard.phase_tail(got[1])
    assert 'class="bar"' in normal and "1/2" in normal


def test_the_real_roadmap_marks_phase_9_retired_and_phase_8_is_the_last_live_one() -> None:
    real = dashboard.read_roadmap(dashboard.ROOT / "ROADMAP.md")
    nine = next(p for p in real if p.num == 9)
    assert nine.state == "retired" and nine.total == 0
    assert dashboard.live_phases(real)[-1].num == 8


def test_the_real_roadmap_has_phase_2_closed_so_it_is_not_the_current_phase() -> None:
    """Phase 2's two blocked lines moved to future scope (KAV-151, ADR-0098), so the board stops
    saying the current phase is 2."""
    real = dashboard.read_roadmap(dashboard.ROOT / "ROADMAP.md")
    two = next(p for p in real if p.num == 2)
    assert two.marker == "x" and two.done == two.total
    assert dashboard.current_phase(real).num != 2


def test_the_real_roadmap_has_version_1_closed() -> None:
    """The clean-up of 2026-11-15 (KAV-153, ADR-0099) closed Phase 4; version 1 closed on
    2026-11-19 (KAV-158, ADR-0105), when Phase 6's last line (KAV-100) and Phase 8's were done.
    Every phase from 0 to 8 is finished, so the board has no phase "in progress", and the
    current phase is the last live one, never a retired one."""
    real = {p.num: p for p in dashboard.read_roadmap(dashboard.ROOT / "ROADMAP.md")}
    for n in range(9):
        assert real[n].marker == "x" and real[n].done == real[n].total, n
    assert dashboard.current_phase(list(real.values())).num == 8
