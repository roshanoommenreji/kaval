"""Slack ChatOps (KAV-55, ADR-0026) against a real Postgres — skipped when no database is
reachable (services/conftest.py). No real Slack connection anywhere here: `WebClient` and
`BaseSocketModeClient` are exercised through small, genuinely-typed fakes, never a mock of
the whole module, so the actual query and decision logic runs for real.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from kaval_gateway import slack_chatops
from kaval_gateway.decisions import DecisionOutcome
from kaval_shared.models import (
    Action,
    BlastRadius,
    Decision,
    Incident,
    PolicyClass,
    Proposal,
    RiskLevel,
    Severity,
    Signal,
    Verdict,
)
from slack_sdk.socket_mode.client import BaseSocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.web import WebClient
from sqlalchemy.orm import Session

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)


class _FakeWebClient(WebClient):
    """A real `WebClient`, never asked to make a network call: `chat_postMessage` is
    overridden to record its arguments instead."""

    def __init__(self) -> None:
        super().__init__(token="xoxb-test")
        self.posted: list[dict[str, Any]] = []

    def chat_postMessage(  # type: ignore[override]  # a narrower double, not a real client
        self, **kwargs: Any
    ) -> dict[str, Any]:
        self.posted.append(kwargs)
        return {"ok": True}


class _FakeSocketClient(BaseSocketModeClient):
    """A real `BaseSocketModeClient` subclass with no WebSocket underneath — just enough to
    satisfy `handle_block_actions`'s two calls: acknowledging and posting through `web_client`.
    """

    def __init__(self, web_client: WebClient) -> None:
        self.web_client = web_client
        self.acked: list[str] = []

    def send_message(self, message: str) -> None:
        self.acked.append(message)


def _action(
    db: Session, *, policy_class: PolicyClass = PolicyClass.ask,
    decided: bool = False, notified: bool = False,
) -> Action:
    target = f"test/{uuid.uuid4()}"
    signal = Signal(source="kubernetes", kind="pod_back_off", target=target,
                     value={}, observed_at=NOW)
    incident = Incident(fingerprint=f"test-{uuid.uuid4()}", severity=Severity.high,
                         opened_at=NOW, signals=[signal])
    proposal = Proposal(incident=incident, summary="restart checkout",
                         root_cause="memory limit too low", confidence=0.82,
                         risk=RiskLevel.low, model="gemma3:1b-it-qat", tokens_in=900,
                         tokens_out=120, cost_usd=Decimal("0.000000"))
    action = Action(proposal=proposal, type="restart_pod", target=target, params={},
                     reversible=True, blast_radius=BlastRadius.pod,
                     policy_class=policy_class,
                     slack_notified_at=NOW if notified else None)
    db.add_all([incident, proposal, action])
    db.flush()
    if decided:
        db.add(Decision(action_id=action.id, verdict=Verdict.approved, actor="someone else",
                          decided_at=NOW))
        db.flush()
    return action


def test_pending_actions_excludes_decided_notified_and_non_ask(db_session: Session) -> None:
    wanted = _action(db_session)
    _action(db_session, decided=True)
    _action(db_session, notified=True)
    _action(db_session, policy_class=PolicyClass.auto)
    _action(db_session, policy_class=PolicyClass.never)

    assert [a.id for a in slack_chatops.pending_actions(db_session)] == [wanted.id]


def test_blocks_carry_the_action_id_on_both_buttons() -> None:
    action = Action(id=uuid.uuid4(), type="restart_pod", target="kaval-demo/checkout",
                     params={}, reversible=True, blast_radius=BlastRadius.pod,
                     policy_class=PolicyClass.ask)
    action.proposal = Proposal(summary="restart checkout", root_cause="oom",
                                confidence=0.9, risk=RiskLevel.low, model="x",
                                tokens_in=1, tokens_out=1, cost_usd=Decimal("0"))
    blocks = slack_chatops._blocks(action)
    buttons = blocks[1]["elements"]
    assert {b["action_id"] for b in buttons} == {"approve", "deny"}
    assert all(b["value"] == str(action.id) for b in buttons)


def test_notify_pass_posts_once_per_pending_action_and_marks_it(db_session: Session) -> None:
    pending = _action(db_session)
    already_done = _action(db_session, notified=True)
    web = _FakeWebClient()

    notified_count = slack_chatops.notify_pass(db_session, web, "C_TEST")

    assert notified_count == 1
    assert len(web.posted) == 1
    assert web.posted[0]["channel"] == "C_TEST"
    assert pending.slack_notified_at is not None
    assert already_done.slack_notified_at == NOW  # untouched, already marked before this pass
    # A second pass finds nothing left — the mark actually took.
    assert slack_chatops.notify_pass(db_session, web, "C_TEST") == 0


def _click_payload(action_id: uuid.UUID, action_id_button: str) -> dict[str, Any]:
    return {
        "type": "block_actions",
        "user": {"id": "U1", "username": "roshan"},
        "actions": [{"action_id": action_id_button, "value": str(action_id)}],
        "channel": {"id": "C_TEST"},
        "message": {"ts": "1234.5678"},
    }


def test_approve_button_records_a_decision_and_acks_and_replies(db_session: Session) -> None:
    action = _action(db_session)
    web = _FakeWebClient()
    client = _FakeSocketClient(web)
    req = SocketModeRequest(
        type="interactive", envelope_id="env-1",
        payload=_click_payload(action.id, "approve"),
    )

    outcome = slack_chatops.handle_block_actions(db_session, client, req)

    assert outcome is DecisionOutcome.recorded
    assert len(client.acked) == 1  # the ack, sent before any DB work
    assert action.decision is not None
    assert action.decision.verdict == Verdict.approved
    assert action.decision.actor == "slack:roshan"
    assert len(web.posted) == 1 and "approved by @roshan" in web.posted[0]["text"]


def test_a_second_click_on_an_already_decided_action_is_ignored_not_an_error(
    db_session: Session,
) -> None:
    action = _action(db_session, decided=True)
    web = _FakeWebClient()
    client = _FakeSocketClient(web)
    req = SocketModeRequest(
        type="interactive", envelope_id="env-2",
        payload=_click_payload(action.id, "deny"),
    )

    outcome = slack_chatops.handle_block_actions(db_session, client, req)

    assert outcome is DecisionOutcome.already_decided
    assert "ignored" in web.posted[0]["text"]


def test_start_is_a_noop_without_slack_env_vars(monkeypatch: Any) -> None:
    for var in ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_CHANNEL_ID"):
        monkeypatch.delenv(var, raising=False)
    assert slack_chatops.configured() is False
    assert slack_chatops.start() is None
