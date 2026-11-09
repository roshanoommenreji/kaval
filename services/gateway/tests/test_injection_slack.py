"""Model-written text on the approval card (KAV-112, ADR-0072). Threat model rows T-SLACK-1 and 2.

The summary a person reads next to the Approve button is written by the model, and the model has
read text an outsider can influence. Two properties matter and neither needs a Slack connection:

1. What Approve approves cannot be changed by that text: the buttons carry the action's own id.
2. The text cannot become Slack markup. Slack treats `<`, `>` and `&` as control characters in a
   message (https://docs.slack.dev/messaging/formatting-message-text/): `<!channel>` pings the whole
   channel, `<https://...|Approve all>` draws a link under any label, `<@U...>` mentions a person.
   The documented fix is to replace the three characters with `&amp;`, `&lt;` and `&gt;`.

The second did not hold until KAV-114 (ADR-0082): the text is now escaped in one place
(`kaval_gateway/slack_text.py`) and every message that carries a summary, an action type or a
target goes through it. The tests below were `xfail(strict=True)` until then.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from kaval_gateway import approvals, health_alerts, slack_chatops
from kaval_shared import health
from kaval_shared.models import (
    Action,
    BlastRadius,
    PolicyClass,
    Proposal,
    RiskLevel,
    Signal,
)

HOSTILE_SUMMARIES = (
    "<!channel> approve this now",
    "<!here> urgent",
    "<!subteam^S0123456789> please look",
    "<@U0123456789> you must approve",
    "<https://example.invalid/login|Approve all fixes>",
    "Fine &amp; safe <b>trust me</b>",
)


def _action(summary: str, *, action_id: uuid.UUID | None = None) -> Action:
    action = Action(id=action_id or uuid.uuid4(), type="restart_pod", target="kaval-demo/checkout",
                    params={}, reversible=True, blast_radius=BlastRadius.pod,
                    policy_class=PolicyClass.ask)
    action.proposal = Proposal(summary=summary, root_cause="x", confidence=0.8,
                               risk=RiskLevel.low, model="x", tokens_in=1, tokens_out=1,
                               cost_usd=Decimal("0"))
    return action


def _card_text(action: Action) -> str:
    return str(slack_chatops._blocks(action)[0]["text"]["text"])


@pytest.mark.parametrize("summary", HOSTILE_SUMMARIES + (str(uuid.uuid4()),))
def test_the_buttons_approve_the_action_they_belong_to_whatever_the_summary_says(
    summary: str,
) -> None:
    mine = uuid.uuid4()
    buttons = slack_chatops._blocks(_action(summary, action_id=mine))[1]["elements"]
    assert {b["value"] for b in buttons} == {str(mine)}
    assert slack_chatops._blocks(_action(summary, action_id=mine))[1]["block_id"] \
        == f"kaval_action:{mine}"


def test_the_button_labels_are_fixed_plain_text() -> None:
    buttons = slack_chatops._blocks(_action("<!channel>"))[1]["elements"]
    assert [(b["text"]["type"], b["text"]["text"]) for b in buttons] == [
        ("plain_text", "Approve"), ("plain_text", "Deny")]


def test_the_summary_is_shown_in_the_card_so_the_escaping_test_below_means_something() -> None:
    assert "restart checkout" in _card_text(_action("restart checkout"))


@pytest.mark.parametrize("summary", HOSTILE_SUMMARIES)
def test_model_written_text_cannot_become_slack_markup(summary: str) -> None:
    text = _card_text(_action(summary))
    assert "<" not in text and ">" not in text


def test_the_summary_stays_readable_after_escaping() -> None:
    # Slack shows &lt; as "<", so a person still reads what the model wrote.
    text = _card_text(_action("free memory < 10% & falling"))
    assert "free memory &lt; 10% &amp; falling" in text


def test_the_action_type_and_target_cannot_become_slack_markup_either() -> None:
    action = _action("restart it")
    action.type = "restart_pod<!channel>"
    action.target = "ns/`<@U0123456789>`<https://example.invalid|Approve all>"
    text = _card_text(action)
    assert "<" not in text and ">" not in text
    # the target sits in a code span: no stray backtick may close it early
    assert text.split("\n")[0].count("`") == 2


def test_the_text_slack_shows_in_notifications_is_escaped_too() -> None:
    action = _action("restart it")
    action.type = "restart_pod"
    action.target = "ns/<!channel>"
    fallback = slack_chatops.fallback_text(action)
    assert "<" not in fallback and ">" not in fallback
    assert fallback.startswith("Approval needed: restart_pod on ")


def test_the_what_will_happen_lines_are_escaped() -> None:
    action = _action("remove the copy")
    action.proposal.model = health.REMOVAL_MODEL
    action.params = {"preview": ["delete <!channel>", "keep & watch"], "undo": "run <@U1> to undo"}
    text = _card_text(action)
    assert "<" not in text and ">" not in text
    assert "keep &amp; watch" in text


def _waiting(target: str) -> Action:
    action = _action("restart it")
    action.target = target
    action.created_at = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    return action


@pytest.mark.parametrize("text_of", [
    lambda a, n, t: approvals._reminder_text(a, n, t),
    lambda a, n, t: approvals._escalation_text(a, n, t),
    lambda a, n, t: approvals._expired_text(a),
])
def test_the_reminder_escalation_and_expiry_messages_escape_the_target(text_of) -> None:  # type: ignore[no-untyped-def]  # noqa: ANN001
    now = datetime(2026, 11, 9, 9, 20, tzinfo=UTC)
    message = text_of(_waiting("ns/<https://example.invalid|Approve all>"), now,
                      approvals.Timing.from_env())
    assert "&lt;https://example.invalid|Approve all&gt;" in message


def test_the_escalation_still_pings_here_because_that_one_is_ours() -> None:
    now = datetime(2026, 11, 9, 9, 40, tzinfo=UTC)
    message = approvals._escalation_text(_waiting("ns/x"), now, approvals.Timing.from_env())
    assert message.count("<!here>") == 1


def test_a_signals_target_cannot_become_markup_in_a_health_alert() -> None:
    signal = Signal(source="kubernetes", kind="kaval_pod_down", target="ns/svc<!channel>",
                    value={}, observed_at=datetime(2026, 11, 9, tzinfo=UTC))
    assert "<!channel>" not in health_alerts.message(signal)
