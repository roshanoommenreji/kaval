"""A phase opens to a short summary and Jira's own list of its stories (KAV-145).

The negative cases matter most: with no login, no network or a broken saved copy the dashboard
must still build, and must say the list is old or missing instead of showing nothing quietly.
"""

from __future__ import annotations

import json
import tomllib
from datetime import date
from pathlib import Path

import dashboard  # type: ignore[import-not-found]  # same folder; pytest puts it on the path

SITE = "https://example.atlassian.net"
EPIC = f"{SITE}/browse/KAV-14"
DAY = date(2026, 11, 13)


def issue(n: int, cat: str, status: str, parent: str = "KAV-14", kind: str = "Story") -> dict:
    return {"key": f"KAV-{n}", "summary": f"Story {n} <b>", "status": status, "cat": cat,
            "type": kind, "parent": parent}


ISSUES = [
    issue(14, "indeterminate", "In Progress", parent="", kind="Epic"),
    issue(110, "done", "Done"),
    issue(144, "new", "To Do"),
    issue(138, "indeterminate", "In Progress"),
    issue(9, "done", "Done", parent="KAV-5"),
]


def boom() -> list[dict]:
    raise FileNotFoundError("no .env here")


def test_a_good_read_is_saved_and_marked_live(tmp_path: Path) -> None:
    cache = tmp_path / "jira.json"
    got = dashboard.read_jira(cache, fetch=lambda: ISSUES, today=DAY)
    assert got["live"] is True and got["fetched"] == "2026-11-13" and got["issues"] == ISSUES
    assert json.loads(cache.read_text(encoding="utf-8"))["issues"] == ISSUES


def test_when_jira_cannot_be_read_the_saved_copy_is_used_with_its_own_date(tmp_path: Path) -> None:
    cache = tmp_path / "jira.json"
    dashboard.read_jira(cache, fetch=lambda: ISSUES, today=date(2026, 11, 1))
    got = dashboard.read_jira(cache, fetch=boom, today=DAY)
    assert got["live"] is False
    assert got["fetched"] == "2026-11-01"  # the day it was really read, never today
    assert got["issues"] == ISSUES
    assert "FileNotFoundError" in got["why"]


def test_a_failed_read_does_not_overwrite_the_saved_copy(tmp_path: Path) -> None:
    cache = tmp_path / "jira.json"
    dashboard.read_jira(cache, fetch=lambda: ISSUES, today=DAY)
    before = cache.read_text(encoding="utf-8")
    dashboard.read_jira(cache, fetch=boom, today=date(2026, 12, 1))
    assert cache.read_text(encoding="utf-8") == before


def test_no_login_and_no_saved_copy_gives_an_empty_list_not_a_crash(tmp_path: Path) -> None:
    got = dashboard.read_jira(tmp_path / "absent.json", fetch=boom, today=DAY)
    assert got == {"fetched": "", "issues": [], "live": False,
                   "why": "FileNotFoundError: no .env here"}


def test_a_broken_saved_copy_is_treated_as_no_copy(tmp_path: Path) -> None:
    cache = tmp_path / "jira.json"
    cache.write_text("{not json", encoding="utf-8")
    assert dashboard.read_jira(cache, fetch=boom, today=DAY)["issues"] == []


def test_the_panel_lists_only_this_epics_stories_open_ones_first() -> None:
    html = dashboard.jira_panel(EPIC, {"fetched": "2026-11-13", "issues": ISSUES})
    order = [html.index(f"/browse/KAV-{n}\"") for n in (138, 144, 110)]
    assert order == sorted(order)  # in progress, then to do, then done
    assert "KAV-9<" not in html  # another epic's story
    assert "3 stories · 1 done · 2 open" in html
    assert "From Jira, read 2026-11-13." in html


def test_every_key_is_a_link_and_titles_are_escaped() -> None:
    html = dashboard.jira_panel(EPIC, {"fetched": "2026-11-13", "issues": ISSUES})
    for n in (14, 110, 138, 144):
        assert f'href="{SITE}/browse/KAV-{n}"' in html
    assert "<b>" not in html and "Story 110 &lt;b&gt;" in html


def test_with_no_issues_the_panel_says_so_and_still_links_the_epic() -> None:
    html = dashboard.jira_panel(EPIC, {"fetched": "", "issues": []})
    assert "not available" in html and f'href="{SITE}/browse/KAV-14"' in html
    assert dashboard.jira_panel(EPIC, None) == html


def test_an_epic_with_no_stories_says_none_yet() -> None:
    html = dashboard.jira_panel(f"{SITE}/browse/KAV-10", {"fetched": "2026-11-13",
                                                           "issues": ISSUES})
    assert "No stories under this epic yet" in html


def test_a_phase_with_no_epic_link_gets_no_jira_block() -> None:
    assert dashboard.jira_panel("", {"fetched": "2026-11-13", "issues": ISSUES}) == ""


def test_every_phase_has_a_short_summary_and_an_epic() -> None:
    cfg = tomllib.loads(
        (dashboard.ROOT / "scripts" / "tracking" / "dashboard.toml").read_text(encoding="utf-8"))
    phases = dashboard.read_roadmap(dashboard.ROOT / "ROADMAP.md")
    for p in phases:
        short = cfg["phase_summary"][str(p.num)]
        assert 40 <= len(short) <= 260, (p.num, len(short))  # short means short
        assert "/browse/KAV-" in cfg["phase_links"][str(p.num)]["jira"]


def test_the_saved_copy_is_never_committed() -> None:
    ignored = (dashboard.ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "docs/jira-stories.json" in ignored
    assert dashboard.JIRA_CACHE == dashboard.ROOT / "docs" / "jira-stories.json"
