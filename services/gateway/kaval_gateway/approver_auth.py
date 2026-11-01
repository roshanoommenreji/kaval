"""The login on the one route that records an approval (KAV-115, ADR-0073, threat T-APPROVE-1).

`POST /v1/actions/{id}/decisions` used to accept anybody who could reach the gateway. Anything
inside the cluster can, so a pod that the untrusted text reached could approve a proposal for
itself. Now the call must carry `Authorization: Bearer <token>`, and the token lives in a
Kubernetes Secret (`APPROVER_TOKEN`) that only the gateway and the people who approve can read.

Three rules, each a test:

* **Closed unless configured.** With no token set (or one shorter than `MIN_TOKEN_LENGTH`) every
  call is refused with 503. A forgotten setting must never mean an open door.
* **Constant-time compare.** `hmac.compare_digest`, so the time a refusal takes does not tell a
  caller how many leading characters were right.
* **The token is never written down.** Not in a log line, not in the refusal's body, not in a
  metric label. A refusal says only *why* in a fixed word.

Slack's buttons do not come through here: that surface connects outward with its own tokens and
calls `record_decision` directly (`slack_chatops`).
"""

from __future__ import annotations

import hmac
import logging
import os
from typing import Annotated

from fastapi import Header, HTTPException, Request
from prometheus_client import Counter

log = logging.getLogger(__name__)

TOKEN_ENV = "APPROVER_TOKEN"
# A random 32-byte token is 43 characters. Anything this short was typed by a person.
MIN_TOKEN_LENGTH = 24

# The label is `outcome`, the one data-free label metrics.py allows besides the service, and its
# value is one of three fixed words, never anything a caller sent (metrics.py's rule).
_REFUSED = Counter(
    "kaval_approval_refused", "Approval calls the gateway refused before reading them", ["outcome"],
)


def configured_token() -> str | None:
    """The token the gateway was given, or None when the route must stay shut."""
    token = os.environ.get(TOKEN_ENV, "").strip()
    return token if len(token) >= MIN_TOKEN_LENGTH else None


def _refuse(request: Request, why: str, status: int, detail: str) -> HTTPException:
    _REFUSED.labels(outcome=why).inc()
    client = request.client.host if request.client else "unknown"
    log.warning("approval call refused (%s) from %s", why, client)
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
    return HTTPException(status_code=status, detail=detail, headers=headers)


def require_approver(
    request: Request, authorization: Annotated[str | None, Header()] = None,
) -> None:
    """FastAPI dependency for the approval route. Raises 503 (shut) or 401 (not you)."""
    expected = configured_token()
    if expected is None:
        raise _refuse(request, "switched_off", 503,
                      "approvals through the API are switched off: no approver token is set")
    scheme, _, given = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not given.strip():
        raise _refuse(request, "missing", 401, "an approver token is required")
    if not hmac.compare_digest(given.strip().encode(), expected.encode()):
        raise _refuse(request, "wrong", 401, "an approver token is required")
