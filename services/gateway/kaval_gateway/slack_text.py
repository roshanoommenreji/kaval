"""The one place text is made safe for Slack (KAV-114, ADR-0082, threat model T-SLACK-2).

Slack reads `<`, `>` and `&` in a message as control characters: `<!channel>` pings everyone,
`<https://...|label>` draws a link under any label, `<@U...>` mentions a person. Its documented
fix is to replace the three with `&amp;`, `&lt;` and `&gt;`, and Slack then shows the characters
as plain text (https://docs.slack.dev/messaging/formatting-message-text/).

Anything in a Slack message that the model wrote, or that a person outside the repository can
influence (a summary, an action type, a target), goes through here. The messages Kaval writes
itself, such as the `<!here>` in an escalation, do not: that one is meant.
"""

from __future__ import annotations


def escape(text: str) -> str:
    """Make `text` show as itself in a Slack message. The ampersand goes first, or the `&` in the
    other two replacements would be escaped a second time."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def code(text: str) -> str:
    """The same, for text that sits between backticks. A backtick inside it would close the code
    span early and let what follows be formatted, so it becomes an apostrophe."""
    return escape(text.replace("`", "'"))
