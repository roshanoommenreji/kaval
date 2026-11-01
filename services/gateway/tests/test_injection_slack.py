"""Model-written text on the approval card (KAV-112, ADR-0072). Threat model rows T-SLACK-1 and 2.

The summary a person reads next to the Approve button is written by the model, and the model has
read text an outsider can influence. Two properties matter and neither needs a Slack connection:

1. What Approve approves cannot be changed by that text: the buttons carry the action's own id.
2. The text cannot become Slack markup. Slack treats `<`, `>` and `&` as control characters in a
   message (https://docs.slack.dev/messaging/formatting-message-text/): `<!channel>` pings the whole
   channel, `<https://...|Approve all>` draws a link under any label, `<@U...>` mentions a person.
   The documented fix is to replace the three characters with `&amp;`, `&lt;` and `&gt;`.

The second does not hold yet, so it is `xfail(strict=True)` against KAV-114: when the fix lands the
test passes, strict mode fails the run, and the marker is removed with the fix.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from kaval_gateway import slack_chatops
from kaval_shared.models import Action, BlastRadius, PolicyClass, Proposal, RiskLevel

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


@pytest.mark.xfail(strict=True, reason="KAV-114: the summary goes into the Slack message as it is, "
                   "so <!channel>, <@user> and <url|label> in it are live Slack markup")
@pytest.mark.parametrize("summary", HOSTILE_SUMMARIES)
def test_model_written_text_cannot_become_slack_markup(summary: str) -> None:
    text = _card_text(_action(summary))
    assert "<" not in text and ">" not in text
