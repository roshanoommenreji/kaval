"""Slack ChatOps: the real approval surface (KAV-55, ADR-0026).

The gateway opens an *outbound* Socket Mode connection to Slack — the same direction every
other integration in this project already uses (the node's security group has zero inbound
rules; access is SSM, never SSH). No inbound endpoint, no Cloudflare Tunnel, no domain, ever.

A background poller finds undecided `ask`-class actions and posts them to a Slack channel with
Approve/Deny buttons. A click arrives over the same outbound connection and is routed to
`record_decision()` — the identical write `POST /v1/actions/{id}/decisions` uses (`KAV-47`), so
there is exactly one way a human decision ever reaches the database, not two.

Disabled by default: `start()` is a no-op unless `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN` and
`SLACK_CHANNEL_ID` are all set — true for every environment until a real Slack App exists, and
for every CI run. `scripts/ops/approve.py` remains the fallback regardless of whether this is
configured.
"""

from __future__ import annotations

import logging
import os
import threading
import uuid
from datetime import UTC, datetime
from typing import Any

from kaval_shared.db import get_engine
from kaval_shared.models import Action, PolicyClass, Verdict
from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.client import BaseSocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse
from slack_sdk.web import WebClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from kaval_gateway.decisions import DecisionOutcome, record_decision

logger = logging.getLogger("kaval_gateway.slack_chatops")

POLL_SECONDS = float(os.environ.get("SLACK_POLL_SECONDS", "30"))


def configured() -> bool:
    return all(
        os.environ.get(var) for var in ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_CHANNEL_ID")
    )


def pending_actions(session: Session) -> list[Action]:
    """`ask`-class actions nobody has decided, that Slack hasn't been told about yet."""
    query = (
        select(Action)
        .where(Action.policy_class == PolicyClass.ask)
        .where(~Action.decision.has())
        .where(Action.slack_notified_at.is_(None))
    )
    return list(session.scalars(query))


def _blocks(action: Action) -> list[dict[str, Any]]:
    proposal = action.proposal
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{action.type}* on `{action.target}`\n{proposal.summary}\n"
                    f"blast radius: `{action.blast_radius.value}` · reversible: "
                    f"`{action.reversible}` · confidence: `{proposal.confidence:.2f}`"
                ),
            },
        },
        {
            "type": "actions",
            "block_id": f"kaval_action:{action.id}",
            "elements": [
                {
                    "type": "button",
                    "style": "primary",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "action_id": "approve",
                    "value": str(action.id),
                },
                {
                    "type": "button",
                    "style": "danger",
                    "text": {"type": "plain_text", "text": "Deny"},
                    "action_id": "deny",
                    "value": str(action.id),
                },
            ],
        },
    ]


def notify_pass(session: Session, web_client: WebClient, channel: str) -> int:
    """One poll pass: post every pending action, mark it notified, return how many.
    Session-injectable so a test can run this against the real query logic without a
    background thread or a real Slack connection — see `tests/test_slack_chatops.py`."""
    actions = pending_actions(session)
    for action in actions:
        web_client.chat_postMessage(
            channel=channel,
            text=f"Approval needed: {action.type} on {action.target}",
            blocks=_blocks(action),
        )
        action.slack_notified_at = datetime.now(UTC)
    session.commit()
    return len(actions)


def _notify_loop(web_client: WebClient, channel: str, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            with Session(get_engine()) as session:
                notify_pass(session, web_client, channel)
        except Exception:
            logger.exception("slack_chatops: notify loop failed this pass")
        stop.wait(POLL_SECONDS)


def handle_block_actions(
    session: Session, client: BaseSocketModeClient, req: SocketModeRequest
) -> DecisionOutcome:
    """A button click, start to finish: acknowledge, record the decision, reply in-thread.
    Session-injectable, same reason as `notify_pass`. Returns the outcome so a test can
    assert on it without parsing the reply text."""
    client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
    clicked = req.payload["actions"][0]
    verdict = Verdict.approved if clicked["action_id"] == "approve" else Verdict.denied
    action_id = uuid.UUID(clicked["value"])
    actor = req.payload.get("user", {}).get("username") or req.payload.get("user", {}).get(
        "id", "slack"
    )
    outcome, _ = record_decision(session, action_id, verdict, actor=f"slack:{actor}")
    text = (
        f"{verdict.value} by @{actor}"
        if outcome is DecisionOutcome.recorded
        else f"ignored — {outcome.value}"
    )
    client.web_client.chat_postMessage(
        channel=req.payload["channel"]["id"],
        thread_ts=req.payload["message"]["ts"],
        text=text,
    )
    return outcome


def _listener(client: BaseSocketModeClient, req: SocketModeRequest) -> None:
    if req.type != "interactive" or req.payload.get("type") != "block_actions":
        client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
        return
    with Session(get_engine()) as session:
        handle_block_actions(session, client, req)


def start() -> threading.Event | None:
    """Starts the outbound Socket Mode connection and the notify poller as background
    threads, and returns the stop `Event` so a test (or a future shutdown hook) can end them
    cleanly. Returns `None` without starting anything if Slack isn't configured — the gateway
    must behave identically to today in that case, which is every environment so far."""
    if not configured():
        logger.info("slack_chatops: SLACK_* env vars not set, ChatOps disabled")
        return None

    bot_token = os.environ["SLACK_BOT_TOKEN"]
    channel = os.environ["SLACK_CHANNEL_ID"]
    web_client = WebClient(token=bot_token)
    socket_client = SocketModeClient(
        app_token=os.environ["SLACK_APP_TOKEN"], web_client=web_client
    )
    socket_client.socket_mode_request_listeners.append(_listener)
    socket_client.connect()

    stop = threading.Event()
    threading.Thread(
        target=_notify_loop, args=(web_client, channel, stop), daemon=True,
        name="slack-chatops-notify",
    ).start()
    logger.info("slack_chatops: connected, notifying #%s every %ss", channel, POLL_SECONDS)
    return stop
