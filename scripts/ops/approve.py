#!/usr/bin/env python3
"""Approve or deny one proposed action, by calling the gateway's `POST /v1/actions/{id}/
decisions` (KAV-47). The fallback approval surface now that Slack ChatOps (`KAV-55`) exists —
a human still decides, it's just a terminal instead of Slack (mobile is deferred to Phase 9).
Nothing here talks to the database directly: it goes through the same API Slack's buttons do
(`kaval_gateway.decisions.record_decision`), so the approval flow is exercised for real.

    make dev-tunnel                                  # in its own terminal
    make approve ACTION=<uuid> VERDICT=approved
    make approve ACTION=<uuid> VERDICT=denied REASON="confidence too low for this blast radius"

    python scripts/ops/approve.py <uuid> approved --actor roshan
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def decide(
    base_url: str, action_id: str, verdict: str, actor: str, reason: str | None,
) -> dict[str, object]:
    body = {"verdict": verdict, "actor": actor}
    if reason:
        body["reason"] = reason
    req = urllib.request.Request(
        f"{base_url}/v1/actions/{action_id}/decisions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result: dict[str, object] = json.loads(resp.read())
            return result
    except urllib.error.HTTPError as exc:
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

    result = decide(args.gateway_url, args.action_id, args.verdict, args.actor, args.reason)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
