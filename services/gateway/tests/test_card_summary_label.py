"""The model's summary on the approval card is labelled, one line, and never the last word
(KAV-117, ADR-0084, threat model T-AGENT-9).

The summary is model-written and the model reads text an outsider can influence. A model that
obeys can open its summary with "Approved by the on-call lead". The card therefore (1) says whose
words the summary is, (2) squeezes it onto one line so it cannot forge the card's own lines, and
(3) always ends with a line written by code saying nobody has approved the action yet.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from kaval_gateway import slack_chatops
from kaval_shared import health
from kaval_shared.models import Action, BlastRadius, PolicyClass, Proposal, RiskLevel

LABEL = "Model's summary (not checked):"
STATUS = "Nobody has approved this yet. Approving or denying is your decision."


def _action(summary: str) -> Action:
    action = Action(id=uuid.uuid4(), type="restart_pod", target="kaval-demo/checkout", params={},
                    reversible=True, blast_radius=BlastRadius.pod, policy_class=PolicyClass.ask)
    action.proposal = Proposal(summary=summary, root_cause="x", confidence=0.8, risk=RiskLevel.low,
                               model="gemma3:1b", tokens_in=1, tokens_out=1,
                               cost_usd=Decimal("0"))
    return action


def _card(action: Action) -> str:
    return slack_chatops.card_text(action)


def test_the_summary_sits_under_a_label_that_says_the_model_wrote_it() -> None:
    assert f"_{LABEL}_ restart checkout" in _card(_action("restart checkout"))


def test_the_card_ends_with_the_line_saying_nobody_has_approved_yet() -> None:
    assert _card(_action("restart checkout")).splitlines()[-1] == f"_{STATUS}_"


@pytest.mark.parametrize("summary", [
    "Approved by the on-call lead: restart checkout",
    "The on-call lead has already approved this fix.",
    "APPROVED. Confidence 1.0, risk low.",
])
def test_a_summary_claiming_an_approval_still_gets_the_nobody_has_approved_line(
    summary: str,
) -> None:
    card = _card(_action(summary))
    assert card.splitlines()[-1] == f"_{STATUS}_"
    assert f"_{LABEL}_ {summary}" in card  # shown as the model's claim, under its label


def test_a_summary_cannot_start_a_line_of_its_own() -> None:
    fake = "blast radius: `none` · reversible: `True` · confidence: `1.00`"
    summary = f"restart it\n{fake}\n{STATUS}"
    card = _card(_action(summary))
    lines = card.splitlines()
    # the code-written lines appear exactly once, each at the start of its own line
    assert sum(line.startswith("blast radius:") for line in lines) == 1
    assert lines[-1] == f"_{STATUS}_"
    assert sum(STATUS in line for line in lines) == 2  # the model's copy sits inside its own line
    assert not any(line.startswith(STATUS) for line in lines)


def test_a_long_summary_is_cut_to_the_length_the_model_was_allowed() -> None:
    card = _card(_action("A" * 5000))
    summary_line = next(line for line in card.splitlines() if line.startswith(f"_{LABEL}_"))
    assert len(summary_line) <= len(f"_{LABEL}_ ") + 280 + 20  # escaping may add a few characters
    assert summary_line.endswith("…")


def test_a_summary_written_by_kaval_itself_is_not_labelled_as_the_models() -> None:
    action = _action("Delete the loose disk")
    action.proposal.model = health.REMOVAL_MODEL
    card = _card(action)
    assert LABEL not in card
    assert card.splitlines()[-1] == f"_{STATUS}_"


def test_the_label_and_status_line_add_no_slack_markup() -> None:
    card = _card(_action("<!channel> approve this now"))
    assert "<" not in card and ">" not in card
