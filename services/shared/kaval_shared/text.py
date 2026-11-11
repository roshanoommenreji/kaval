"""Text from outside, made into one safe line (KAV-113, KAV-117; ADR-0083, ADR-0084).

Two places show a person or a model a line of text that someone outside the repository can
influence: the model reads a signal's message (agent), and a person reads the model's summary on
the approval card (gateway). In both, a line break would let that text start a line of its own and
pass for one of the message's own headings or code-written lines. One cleaner serves both; the
gateway must not import the agent, so it lives here.
"""

from __future__ import annotations

import unicodedata

CUT_MARK = "…"
# Control characters (Cc) and the two Unicode line/paragraph separators (Zl, Zp) count as blanks.
# Invisible format characters (Cf, e.g. a zero-width space) stay: they cannot start a line.
_BLANK_CATEGORIES = frozenset({"Cc", "Zl", "Zp"})


def flatten_line(text: str, limit: int) -> str:
    """One line, at most `limit` characters.

    Every kind of line break, tab and control character becomes a space and runs of blanks
    collapse, so the text can never start a line of its own. A text longer than `limit` (measured
    after flattening) is cut and ends in "…", so a reader can see it was cut."""
    flat = " ".join(
        "".join(" " if ch.isspace() or unicodedata.category(ch) in _BLANK_CATEGORIES else ch
                for ch in text).split()
    )
    if len(flat) <= limit:
        return flat
    return flat[: limit - len(CUT_MARK)].rstrip() + CUT_MARK
