"""Syncing docs/runbooks/*.md into runbook_chunk: idempotent, and re-embeds only what changed
(KAV-40). No __init__.py here, like the other services' tests.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest
from kaval_agent import index_runbooks as ir
from kaval_shared.models import RUNBOOK_EMBED_DIM, RunbookChunk
from sqlalchemy import delete, select
from sqlalchemy.orm import Session


def _clear(db_session: Session) -> None:
    """`sync_runbooks` deletes whatever's in the table but not in the run's `current` set —
    correct sync behaviour, but it means a real indexing run against a shared dev database
    (Lab 10) would otherwise leave rows these tests see and correctly-but-confusingly treat
    as stale. Cleared inside `db_session`'s own rolled-back transaction, so nothing real is
    actually touched. Only the tests that write full sync results call this; the read-only
    and pure tests above don't need a database at all."""
    db_session.execute(delete(RunbookChunk))
    db_session.flush()


class CountingEmbed:
    """A fake embed_fn: deterministic, and counts how many chunks it was actually asked to
    embed, so a test can assert unchanged chunks were never re-sent to Ollama."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    def __call__(self, texts: Sequence[str]) -> list[list[float]]:
        self.texts.extend(texts)
        return [[0.1] * RUNBOOK_EMBED_DIM for _ in texts]


ONE_RUNBOOK = """\
# A runbook

## Signals

Something is wrong.

## Remediation

Fix it.
"""


def test_repo_root_finds_the_real_checkout() -> None:
    root = ir.repo_root()
    assert (root / "pyproject.toml").is_file()
    assert (root / "docs" / "runbooks").is_dir()


def test_read_runbooks_skips_the_format_readme(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Format\n\n## Not a runbook\n\nformat spec\n")
    (tmp_path / "one.md").write_text(ONE_RUNBOOK)
    chunks = ir.read_runbooks(tmp_path)
    assert set(chunks) == {("one.md", "Signals"), ("one.md", "Remediation")}


def test_read_runbooks_exits_clearly_when_the_directory_is_missing(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="Lab 10"):
        ir.read_runbooks(tmp_path / "nope")


def test_a_first_run_creates_every_chunk_and_a_second_touches_nothing(
    db_session: Session, tmp_path: Path,
) -> None:
    _clear(db_session)
    (tmp_path / "one.md").write_text(ONE_RUNBOOK)
    current = ir.read_runbooks(tmp_path)

    embed_fn = CountingEmbed()
    first = ir.sync_runbooks(db_session, current, embed_fn)
    assert set(first.created) == {"one.md — Signals", "one.md — Remediation"}
    assert not first.updated and not first.unchanged and not first.deleted
    assert len(embed_fn.texts) == 2

    embed_fn2 = CountingEmbed()
    second = ir.sync_runbooks(db_session, current, embed_fn2)
    assert set(second.unchanged) == {"one.md — Signals", "one.md — Remediation"}
    assert not second.created and not second.updated and not second.deleted
    assert embed_fn2.texts == []  # nothing changed: no chunk was re-embedded


def test_an_edited_chunk_re_embeds_only_itself(db_session: Session, tmp_path: Path) -> None:
    _clear(db_session)
    (tmp_path / "one.md").write_text(ONE_RUNBOOK)
    ir.sync_runbooks(db_session, ir.read_runbooks(tmp_path), CountingEmbed())

    edited = ONE_RUNBOOK.replace("Fix it.", "Fix it differently.")
    (tmp_path / "one.md").write_text(edited)
    embed_fn = CountingEmbed()
    result = ir.sync_runbooks(db_session, ir.read_runbooks(tmp_path), embed_fn)

    assert result.updated == ["one.md — Remediation"]
    assert result.unchanged == ["one.md — Signals"]
    assert embed_fn.texts == ["A runbook — Remediation\n\nFix it differently."]


def test_a_removed_heading_is_deleted(db_session: Session, tmp_path: Path) -> None:
    _clear(db_session)
    (tmp_path / "one.md").write_text(ONE_RUNBOOK)
    ir.sync_runbooks(db_session, ir.read_runbooks(tmp_path), CountingEmbed())

    (tmp_path / "one.md").write_text("# A runbook\n\n## Signals\n\nSomething is wrong.\n")
    result = ir.sync_runbooks(db_session, ir.read_runbooks(tmp_path), CountingEmbed())

    assert result.deleted == ["one.md — Remediation"]
    remaining = db_session.scalars(select(RunbookChunk.heading)).all()
    assert set(remaining) == {"Signals"}


def test_a_removed_file_deletes_all_its_chunks(db_session: Session, tmp_path: Path) -> None:
    _clear(db_session)
    (tmp_path / "one.md").write_text(ONE_RUNBOOK)
    ir.sync_runbooks(db_session, ir.read_runbooks(tmp_path), CountingEmbed())

    (tmp_path / "one.md").unlink()
    result = ir.sync_runbooks(db_session, ir.read_runbooks(tmp_path), CountingEmbed())

    assert set(result.deleted) == {"one.md — Signals", "one.md — Remediation"}
    assert db_session.scalars(select(RunbookChunk)).all() == []
