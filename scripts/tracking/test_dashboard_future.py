"""The dashboard's Beyond v1 section is read from docs/future-scope.md, not typed in (KAV-99)."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import dashboard  # type: ignore[import-not-found]  # same folder; pytest puts it on the path

PAGE = """# Future scope

## Candidate work

### FS-1 — Integration mini-projects (highest value)

Two weekend-sized builds that wire the agent into a **real third-party system**. The point is
the plumbing reflex, not the polish.

- Candidate A: something

### FS-2 — Petti: a box for the cloud

Jira epic: [KAV-99](https://example.atlassian.net/browse/KAV-99). Planned 2026-10-28.
**Not started yet.** It would be its own repository.

**The idea.** A standard box for the cloud. Drop any project in and it comes up. One more
sentence that is long enough, with plenty of extra words in it, to push the summary well past
the limit that the dashboard keeps every card's summary under. A fourth that must never be shown.

---

## Verdict

FS-1 matters most. This text belongs to no idea.
"""


def ideas(tmp_path: Path) -> list[dict[str, Any]]:
    f = tmp_path / "future-scope.md"
    f.write_text(PAGE, encoding="utf-8")
    return dashboard.read_future_scope(f)


def test_each_fs_heading_becomes_one_idea_in_order(tmp_path: Path) -> None:
    got = ideas(tmp_path)
    assert [(i["id"], i["title"]) for i in got] == [
        ("FS-1", "Integration mini-projects (highest value)"),
        ("FS-2", "Petti: a box for the cloud"),
    ]


def test_the_summary_is_the_first_paragraph_joined_onto_one_line(tmp_path: Path) -> None:
    first = ideas(tmp_path)[0]
    assert first["summary"] == (
        "Two weekend-sized builds that wire the agent into a **real third-party system**. "
        "The point is the plumbing reflex, not the polish."
    )


def test_the_jira_line_is_a_link_not_the_summary(tmp_path: Path) -> None:
    petti = ideas(tmp_path)[1]
    assert petti["jira_key"] == "KAV-99"
    assert petti["jira"] == "https://example.atlassian.net/browse/KAV-99"
    assert not petti["summary"].startswith("Jira")
    assert "**The idea.**" not in petti["summary"]
    assert "own repository" not in petti["summary"]  # the rest of the Jira paragraph is skipped too


def test_a_long_summary_is_cut_at_a_whole_sentence(tmp_path: Path) -> None:
    summary = ideas(tmp_path)[1]["summary"]
    assert summary.endswith(".")
    assert "never be shown" not in summary
    assert len(summary) <= dashboard.FUTURE_SUMMARY_LIMIT


def test_the_label_is_dropped_and_the_sentences_before_the_limit_are_kept(tmp_path: Path) -> None:
    assert ideas(tmp_path)[1]["summary"] == (
        "A standard box for the cloud. Drop any project in and it comes up."
    )


def test_one_sentence_longer_than_the_limit_is_cut_at_a_word() -> None:
    long = "word " * 100
    cut = dashboard.card_summary(long.strip() + ".")
    assert cut.endswith("…") and len(cut) <= dashboard.FUTURE_SUMMARY_LIMIT
    assert not cut[:-1].endswith(" ")


def test_an_idea_with_no_epic_has_no_jira_link(tmp_path: Path) -> None:
    first = ideas(tmp_path)[0]
    assert first["jira"] == "" and first["jira_key"] == ""


def test_text_after_the_last_idea_is_not_swept_into_it(tmp_path: Path) -> None:
    assert all("belongs to no idea" not in i["summary"] for i in ideas(tmp_path))


def test_the_anchor_matches_the_one_github_makes() -> None:
    assert dashboard.github_anchor("FS-6 — Petti: a box for the cloud") == (
        "fs-6--petti-a-box-for-the-cloud"
    )
    assert dashboard.github_anchor("FS-1 — Integration mini-projects (highest value)") == (
        "fs-1--integration-mini-projects-highest-value"
    )


def test_a_missing_page_gives_no_section_rather_than_an_error(tmp_path: Path) -> None:
    assert dashboard.read_future_scope(tmp_path / "absent.md") == []


def test_every_real_idea_is_read_and_petti_carries_its_epic() -> None:
    page = dashboard.ROOT / "docs" / "future-scope.md"
    got = dashboard.read_future_scope(page)
    lines = page.read_text(encoding="utf-8").splitlines()
    headings = [ln for ln in lines if ln.startswith("### FS-")]
    assert len(got) == len(headings) >= 6
    assert all(i["summary"] for i in got)
    assert {i["id"]: i["jira_key"] for i in got}["FS-6"] == "KAV-99"


def test_future_ideas_never_count_toward_progress() -> None:
    phases = dashboard.read_roadmap(dashboard.ROOT / "ROADMAP.md")
    assert all("FS-" not in text for p in phases for _, text in p.tasks)


def page(future: list[dict[str, Any]]) -> str:
    """The real dashboard, built from the repo as `make dashboard` does, minus the git log."""
    root = dashboard.ROOT
    docs = root / "docs"

    def toml(path: Path) -> dict[str, Any]:
        return tomllib.loads(path.read_text(encoding="utf-8"))

    sessions, threads = dashboard.read_journal(docs / "journal")
    return dashboard.render(
        toml(root / "scripts" / "tracking" / "dashboard.toml"),
        dashboard.read_roadmap(root / "ROADMAP.md"), dashboard.read_adrs(docs / "adr"),
        dashboard.read_labs(docs / "labs"), sessions, threads, [],
        dashboard.read_learn(docs / "learn"), toml(docs / "architecture" / "architecture.toml"),
        dashboard.read_releases(docs / "releases"), future,
    )


def test_the_page_shows_every_idea_with_its_links_and_the_same_progress() -> None:
    future = dashboard.read_future_scope(dashboard.ROOT / "docs" / "future-scope.md")
    with_ideas, without = page(future), page([])
    assert "<h2>Beyond v1</h2>" in with_ideas and "<h2>Beyond v1</h2>" not in without
    assert with_ideas.count('<div class="fs">') == len(future)
    assert "future-scope.md#fs-6--petti-a-box-for-the-cloud" in with_ideas
    assert "browse/KAV-99" in with_ideas
    readout = with_ideas.split('<dl class="readout">')[1].split("</dl>")[0]
    assert readout == without.split('<dl class="readout">')[1].split("</dl>")[0]
