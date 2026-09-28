"""Sync `docs/runbooks/*.md` into `runbook_chunk` (KAV-40, ADR-0015).

    make index-runbooks
    python -m kaval_agent.index_runbooks

A **laptop-run tool**, like `scripts/tracking/jira-sync.py`: it reads the repo checkout
directly and needs `make dev-tunnel` for Postgres and Ollama. It does not run inside the
agent's Docker image — `docs/runbooks/` isn't in that image (the root `.dockerignore`
allowlist deliberately keeps everything but `pyproject.toml`, `uv.lock`, `services/` and
`migrations/` off the daemon), so a runbook edit can't take effect until someone re-indexes
it, the same way a code change can't take effect until someone rebuilds. See Lab 10.

Idempotent, matched by `(path, heading)`: a second run with no doc changes re-embeds nothing
and reports every chunk `unchanged`. An edited heading re-embeds only that chunk. A removed
heading or file is deleted from the table, so the index never drifts ahead of the docs.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from kaval_shared.models import RunbookChunk
from sqlalchemy import select
from sqlalchemy.orm import Session

from kaval_agent.embeddings import embed
from kaval_agent.runbooks import Chunk, chunk_runbook

EmbedFn = Callable[[Sequence[str]], list[list[float]]]


def repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError("no pyproject.toml found above this file — not a full checkout?")


def read_runbooks(runbooks_dir: Path) -> dict[tuple[str, str], Chunk]:
    """Every `## ` chunk of every runbook, keyed by `(filename, heading)`. `README.md` is
    the format spec, not a runbook, and is skipped."""
    if not runbooks_dir.is_dir():
        sys.exit(
            f"{runbooks_dir} doesn't exist. This runs from a full checkout on the laptop, "
            "not inside the agent's Docker image — see Lab 10."
        )
    chunks: dict[tuple[str, str], Chunk] = {}
    for path in sorted(runbooks_dir.glob("*.md")):
        if path.name == "README.md":
            continue
        for chunk in chunk_runbook(path.read_text(encoding="utf-8")):
            chunks[(path.name, chunk.heading)] = chunk
    return chunks


@dataclass
class SyncResult:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)


def sync_runbooks(
    session: Session, current: dict[tuple[str, str], Chunk], embed_fn: EmbedFn = embed
) -> SyncResult:
    """Write `current` into `runbook_chunk`, embedding only what changed, then delete
    whatever's left that isn't in `current` any more. One transaction: a run lands whole."""
    existing = {(c.path, c.heading): c for c in session.scalars(select(RunbookChunk)).all()}
    result = SyncResult()
    to_write: list[tuple[str, Chunk]] = []
    for key, chunk in current.items():
        label = f"{key[0]} — {key[1]}"
        old = existing.get(key)
        if old is not None and old.content_hash == chunk.content_hash:
            result.unchanged.append(label)
        else:
            to_write.append((label, chunk))

    if to_write:
        vectors = embed_fn([chunk.content for _, chunk in to_write])
        for (label, chunk), vector in zip(to_write, vectors, strict=True):
            path, heading = label.split(" — ", 1)
            old = existing.get((path, heading))
            if old is None:
                session.add(RunbookChunk(
                    path=path, heading=heading, ordinal=chunk.ordinal, content=chunk.content,
                    content_hash=chunk.content_hash, embedding=vector,
                ))
                result.created.append(label)
            else:
                old.ordinal, old.content = chunk.ordinal, chunk.content
                old.content_hash, old.embedding = chunk.content_hash, vector
                result.updated.append(label)

    for key in existing.keys() - current.keys():
        session.delete(existing[key])
        result.deleted.append(f"{key[0]} — {key[1]}")

    session.commit()
    return result


def main() -> int:
    runbooks_dir = repo_root() / "docs" / "runbooks"
    current = read_runbooks(runbooks_dir)
    if not current:
        sys.exit(f"no runbooks found in {runbooks_dir}")

    from kaval_shared.db import get_engine  # only a real run needs the database

    with Session(get_engine()) as session:
        result = sync_runbooks(session, current)

    for label, items in (
        ("created", result.created), ("updated", result.updated),
        ("unchanged", result.unchanged), ("deleted", result.deleted),
    ):
        if items:
            print(f"{label:<10} {len(items)}")
            for name in items:
                print(f"  {name}")
    if not any((result.created, result.updated, result.deleted)):
        print("nothing changed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
