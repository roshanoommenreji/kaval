"""Tests for reading a change record into a Jira Release (KAV-35)."""

from __future__ import annotations

from pathlib import Path

import jira_release as jr
import pytest

RECORD = """# Release v0.3.0 — 2026-11-02

Kaval 0.3.0: gateway 1.2.0 · collector 0.4.1 · agent 0.1.0

- **Environment:** prod

## Components

| Component | Version |
|---|---|

## Issues

KAV-51, KAV-9, KAV-51, KAV-100

## Risk

**low**
"""


def test_reads_name_description_date_and_issues_in_number_order() -> None:
    rec = jr.parse(RECORD)
    assert rec.name == "Kaval 0.3.0"
    assert rec.description == "Kaval 0.3.0: gateway 1.2.0 · collector 0.4.1 · agent 0.1.0"
    assert rec.date == "2026-11-02"
    assert rec.issues == ("KAV-9", "KAV-51", "KAV-100")


def test_a_release_that_names_no_issue_has_none() -> None:
    text = RECORD.replace("KAV-51, KAV-9, KAV-51, KAV-100", "None: no commit names a Jira key.")
    assert jr.parse(text).issues == ()


def test_a_file_that_is_not_a_record_is_refused() -> None:
    with pytest.raises(ValueError, match="not a change record"):
        jr.parse("# Something else\n")
    with pytest.raises(ValueError, match="components"):
        jr.parse("# Release v0.3.0 — 2026-11-02\n\nno bill of materials\n")


def test_finds_the_record_with_or_without_the_v(tmp_path: Path) -> None:
    (tmp_path / "2026-11-02-v0.3.0.md").write_text("x", encoding="utf-8")
    (tmp_path / "2026-10-09-v0.1.0.md").write_text("x", encoding="utf-8")
    assert jr.find_record(tmp_path, "0.3.0").name == "2026-11-02-v0.3.0.md"
    assert jr.find_record(tmp_path, "v0.1.0").name == "2026-10-09-v0.1.0.md"
    with pytest.raises(FileNotFoundError):
        jr.find_record(tmp_path, "9.9.9")
    with pytest.raises(ValueError):
        jr.find_record(tmp_path, "latest")


def test_the_real_baseline_record_still_parses() -> None:
    root = Path(__file__).resolve().parents[2] / "docs" / "releases"
    rec = jr.parse(jr.find_record(root, "0.1.0").read_text(encoding="utf-8"))
    assert rec.name == "Kaval 0.1.0"
    assert "KAV-32" in rec.issues
