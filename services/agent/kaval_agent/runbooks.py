"""Splitting a runbook into retrievable chunks (KAV-40, ADR-0015).

`docs/runbooks/README.md`'s format is fixed: a `# Title`, then `## Signals`, `## Likely
causes`, `## Diagnosis`, `## Remediation`, `## Do not` (a runbook may also add its own, like
`## Related`). One chunk per `## ` section is the retrieval unit — coarser than a paragraph,
finer than a whole runbook, and it matches how a person actually reads one: "what's the
diagnosis" is a different question from "what's the fix".

Pure and file-format-only: no database, no embedding call. `kaval_agent.index_runbooks` calls
this, then embeds and stores what it returns.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_H1 = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_H2 = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Chunk:
    heading: str
    ordinal: int  # position within the file, so retrieval results can be shown in doc order
    content: str  # "{title} — {heading}\n\n{body}": what gets embedded and stored
    content_hash: str  # sha256(content), hex: unchanged content re-uses its old embedding


def chunk_runbook(text: str) -> list[Chunk]:
    """One `Chunk` per `## ` section, in file order.

    The `# Title` line (and anything before the first `## `, normally a short lead-in
    paragraph) isn't a chunk of its own — nothing would retrieve a title by itself — but it's
    prefixed onto every chunk's content, so a chunk reads sensibly out of context:
    "Database lost or corrupted — Likely causes\\n\\n1. ..." rather than starting mid-list.
    A `## ` heading with nothing under it produces no chunk: there's nothing to embed.
    """
    title_match = _H1.search(text)
    title = title_match.group(1).strip() if title_match else ""
    headings = list(_H2.finditer(text))
    chunks = []
    for i, m in enumerate(headings):
        heading = m.group(1).strip()
        start = m.end()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        body = text[start:end].strip()
        if not body:
            continue
        prefix = f"{title} — {heading}" if title else heading
        content = f"{prefix}\n\n{body}"
        chunks.append(Chunk(
            heading=heading, ordinal=i, content=content,
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        ))
    return chunks
