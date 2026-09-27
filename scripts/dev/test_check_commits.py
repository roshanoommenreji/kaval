"""Tests for the commit-message check (KAV-25)."""

from __future__ import annotations

import pytest
from check_commits import problems

STORY = "feat/KAV-25-commit-conventions"


@pytest.mark.parametrize(
    "message",
    [
        "feat: KAV-25 commit-msg hook",
        "fix(ci): KAV-24 pin the Trivy checksum",
        "infra!: KAV-31 move the database to its own server",
        "docs: KAV-25 Lab 07\n\nBody text with #hashtag and a PR ref #1.",
        "Merge pull request #2 from roshanoommenreji/feat/KAV-25-commit-conventions",
        "Merge branch 'feat/KAV-23-gateway-signals'",
        'Revert "feat: KAV-25 commit-msg hook"',
        "# Please enter the commit message\nchore: KAV-25 tidy\n# comment line",
    ],
)
def test_accepts(message: str) -> None:
    assert problems(message, STORY) == []


def test_housekeeping_branch_needs_no_key() -> None:
    assert problems("chore: tidy the root", "chore/repo-structure") == []


@pytest.mark.parametrize(
    ("message", "fragment"),
    [
        ("added the hook", "must start with"),
        ("feature: KAV-25 hook", "must start with"),
        ("feat:KAV-25 no space", "must start with"),
        ("Feat: KAV-25 capital", "must start with"),
        ("feat: add the hook", "needs its KAV key"),
        ("feat: KAV-25 " + "x" * 100, "keep it to 100"),
        ("feat: KAV-25 hook #comment done", "smart-commit"),
        ("feat: KAV-25 hook\n\nKAV-25 #time 2h", "smart-commit"),
        ("feat: KAV-25 #done", "smart-commit"),
        ("", "empty"),
        ("# only comments", "empty"),
    ],
)
def test_rejects(message: str, fragment: str) -> None:
    found = problems(message, STORY)
    assert any(fragment in p for p in found), found


def test_autosquash_allowed_locally_refused_in_ci() -> None:
    message = "fixup! feat: KAV-25 hook"
    assert problems(message, STORY) == []
    assert problems(message, STORY, in_ci=True)
