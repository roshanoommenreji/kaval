"""The one place text is made safe for Slack (KAV-114, ADR-0082). No Slack, no database.

Slack reads `<`, `>` and `&` in a message as control characters, so the three are replaced with
`&amp;`, `&lt;` and `&gt;` (https://docs.slack.dev/messaging/formatting-message-text/). Text that
sits inside a `code span` also loses its backticks, because one would close the span early."""

from __future__ import annotations

import pytest
from kaval_gateway import slack_text


@pytest.mark.parametrize(("raw", "safe"), [
    ("<!channel>", "&lt;!channel&gt;"),
    ("<@U0123456789>", "&lt;@U0123456789&gt;"),
    ("<https://x.invalid/a|Approve all>", "&lt;https://x.invalid/a|Approve all&gt;"),
    ("fish & chips", "fish &amp; chips"),
    # escaped once, at the edge, never twice
    ("already &amp; escaped", "already &amp;amp; escaped"),
])
def test_escape_replaces_the_three_control_characters(raw: str, safe: str) -> None:
    assert slack_text.escape(raw) == safe


@pytest.mark.parametrize("plain", [
    "restart checkout", "memory at 91% (limit 1Gi)", "it's *fine* and _calm_", "",
    "kaval-demo/checkout",
])
def test_ordinary_text_is_left_exactly_as_it_was(plain: str) -> None:
    assert slack_text.escape(plain) == plain


def test_a_less_than_sign_in_a_sentence_survives_as_readable_text() -> None:
    # What Slack shows for &lt; is "<", so nothing is lost; this is why we escape rather than strip.
    assert slack_text.escape("free memory < 10%") == "free memory &lt; 10%"


def test_code_text_cannot_close_its_own_code_span() -> None:
    assert "`" not in slack_text.code("pod`<!channel>`x")
    assert slack_text.code("pod`<!channel>`x") == "pod'&lt;!channel&gt;'x"


def test_code_text_that_is_ordinary_is_unchanged() -> None:
    assert slack_text.code("kaval-demo/checkout") == "kaval-demo/checkout"
