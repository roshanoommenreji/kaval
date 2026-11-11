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

from kaval_shared.text import flatten_line


def escape(text: str) -> str:
    """Make `text` show as itself in a Slack message. The ampersand goes first, or the `&` in the
    other two replacements would be escaped a second time."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# KAV-117, ADR-0084, threat model T-AGENT-9. The summary is the one place a person reads text the
# model wrote, and the model has read text an outsider can influence. These two lines are written
# by code, so the model cannot change them or leave them out.
SUMMARY_LABEL = "Model's summary (not checked):"
KAVAL_SUMMARY_LABEL = "Summary:"  # for a proposal Kaval's own rules wrote (the removal planner)
NOT_APPROVED_LINE = "Nobody has approved this yet. Approving or denying is your decision."
SUMMARY_LIMIT = 280  # what `kaval_agent.schema.Diagnosis.summary` allows the model to write


def summary_line(summary: str, *, by_model: bool) -> str:
    """The summary as one labelled line. It is flattened first, so it cannot start a line of its
    own and pass for the card's blast-radius line or the not-approved line below it, and then
    escaped like all model-written text (`escape`)."""
    label = SUMMARY_LABEL if by_model else KAVAL_SUMMARY_LABEL
    return f"_{label}_ {escape(flatten_line(summary, SUMMARY_LIMIT))}"


def not_approved_line() -> str:
    return f"_{NOT_APPROVED_LINE}_"


def code(text: str) -> str:
    """The same, for text that sits between backticks. A backtick inside it would close the code
    span early and let what follows be formatted, so it becomes an apostrophe."""
    return escape(text.replace("`", "'"))
