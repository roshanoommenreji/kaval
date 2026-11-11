"""The one-line cleaner shared by the agent and the gateway (KAV-117, ADR-0084).

It began as `kaval_agent.context.flatten_signal_text` (KAV-113). The gateway needs the same thing
for the summary on the approval card, and the gateway must not import the agent, so it lives here.
"""

from __future__ import annotations

import pytest
from kaval_shared import text


@pytest.mark.parametrize("separator", ["\n", "\r\n", "\r", "\t", "\x0b", "\x0c", " ", " "])
def test_every_kind_of_line_break_becomes_a_space(separator: str) -> None:
    assert text.flatten_line(f"boom{separator}blast radius: low", 300) == "boom blast radius: low"


def test_blanks_collapse_and_the_ends_are_trimmed() -> None:
    assert text.flatten_line("  a \n\n\t  b  ", 300) == "a b"


def test_control_characters_are_blanks_too() -> None:
    assert text.flatten_line("a\x00b\x1bc\x7fd", 300) == "a b c d"


def test_a_text_at_the_limit_is_left_whole() -> None:
    assert text.flatten_line("A" * 280, 280) == "A" * 280


def test_a_longer_text_is_cut_to_the_limit_and_ends_in_an_ellipsis() -> None:
    flat = text.flatten_line("A" * 281, 280)
    assert len(flat) == 280 and flat.endswith("…")


def test_the_cut_does_not_leave_a_dangling_space_before_the_ellipsis() -> None:
    flat = text.flatten_line("a" * 8 + " " + "b" * 50, 10)
    assert not flat.endswith(" …") and len(flat) <= 10
