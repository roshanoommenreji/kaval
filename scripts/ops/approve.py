#!/usr/bin/env python3
"""Approve or deny one proposed action, by calling the gateway's `POST /v1/actions/{id}/
decisions` (KAV-47). The fallback approval surface now that Slack ChatOps (`KAV-55`) exists —
a human still decides, it's just a terminal instead of Slack (mobile is deferred to Phase 9).
Nothing here talks to the database directly: it goes through the same API Slack's buttons do
(`kaval_gateway.decisions.record_decision`), so the approval flow is exercised for real.

    make dev-tunnel                                  # in its own terminal
    export KAVAL_APPROVER_TOKEN=...                  # the gateway's APPROVER_TOKEN (KAV-115)
    make approve ACTION=<uuid> VERDICT=approved
    make approve ACTION=<uuid> VERDICT=denied REASON="confidence too low for this blast radius"

    python scripts/ops/approve.py <uuid> approved --actor roshan
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import sys
import urllib.error
import urllib.request
from typing import IO

TOKEN_ENV = "KAVAL_APPROVER_TOKEN"
_REDIRECTS = (301, 302, 303, 307, 308)  # the answers that say "go there instead"


class _NeverFollow(urllib.request.HTTPRedirectHandler):
    """urllib's redirect handler, told never to follow. Left alone it repeats the request at the
    address a redirect names and copies its headers, so the approver token would go with it
    (KAV-124, ADR-0075). It is the rule scripts/tracking/atlassian_http.py applies to the Atlassian
    login and scripts/release/promote.py to the Jira read, copied here and not imported, so each
    script stays self-contained."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: http.client.HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        return None


_OPENER = urllib.request.build_opener(_NeverFollow)


def decide(
    base_url: str, action_id: str, verdict: str, actor: str, reason: str | None,
    token: str,
) -> dict[str, object]:
    body = {"verdict": verdict, "actor": actor}
    if reason:
        body["reason"] = reason
    req = urllib.request.Request(
        f"{base_url}/v1/actions/{action_id}/decisions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        with _OPENER.open(req, timeout=10) as resp:
            result: dict[str, object] = json.loads(resp.read())
            return result
    except urllib.error.HTTPError as exc:
        if exc.code in _REDIRECTS:
            # Never followed, because the token would go with it. It names neither the token nor
            # where the answer pointed. The first request did reach the gateway, so say so.
            exc.close()
            raise SystemExit(
                f"the gateway answered with a redirect (HTTP {exc.code}). It was not followed, so "
                "the approver token stays on this address. The decision may already have been "
                "recorded: look before running it again. If the gateway has moved, update "
                "KAVAL_GATEWAY_URL."
            ) from None
        detail = exc.read().decode(errors="replace")
        sys.exit(f"{exc.code} {exc.reason}: {detail}")
    except urllib.error.URLError as exc:
        sys.exit(f"could not reach the gateway at {base_url}: {exc.reason}\n"
                 f"(is `make dev-tunnel` running?)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scripts/ops/approve.py",
        description="Approve or deny one proposed action through the gateway's decision "
                     "endpoint (KAV-47) — the Phase-3 stand-in for the mobile app.",
    )
    parser.add_argument("action_id", help="the action's uuid (from /v1/incidents/{id})")
    parser.add_argument("verdict", choices=("approved", "denied"))
    parser.add_argument("--actor", default=os.environ.get("USER", "cli"),
                        help="who decided; defaults to $USER, falls back to 'cli'")
    parser.add_argument("--reason")
    parser.add_argument("--gateway-url",
                        default=os.environ.get("KAVAL_GATEWAY_URL", "http://localhost:8000"))
    args = parser.parse_args(argv)

    # From the environment only, never an argument: a command line is kept in shell history.
    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token:
        sys.exit(f"set {TOKEN_ENV} to the gateway's approver token first (it is never a "
                 "command-line option, so it stays out of your shell history).")
    result = decide(args.gateway_url, args.action_id, args.verdict, args.actor, args.reason,
                    token)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
