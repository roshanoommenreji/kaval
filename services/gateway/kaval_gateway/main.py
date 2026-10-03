"""The gateway app: `/healthz` (KAV-22) plus the read-only /v1 REST surface (KAV-23).

OpenAPI is generated from the code: interactive docs at /docs, the schema at /openapi.json.
`/healthz` stays outside /v1 because probes aren't part of the versioned API contract.

The health check answers the two questions that matter before anything else can work: is the
database reachable *and migrated*, and is the local model actually pulled, not merely is
Ollama up.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

import httpx
from fastapi import FastAPI, Response
from kaval_shared.db import get_engine
from pydantic import BaseModel
from sqlalchemy import text

from kaval_gateway import __version__, slack_chatops
from kaval_gateway.api import router


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    # No-op unless SLACK_BOT_TOKEN/SLACK_APP_TOKEN/SLACK_CHANNEL_ID are all set — true for
    # every environment so far, including every CI run (kaval_gateway.slack_chatops).
    slack_chatops.start()
    yield


app = FastAPI(
    title="Kaval gateway",
    lifespan=_lifespan,
    version=__version__,  # the gateway component's version, not the product's (ADR-0013)
    summary="The operator-facing API of an ops agent that acts only with human approval.",
    description=(
        "Signals as collected, incidents with their full timeline (proposals, actions, "
        "decisions, executions, outcomes), and one write: approve or deny a proposed "
        "action (KAV-47). No inbound exposure: the server binds to 127.0.0.1 and is "
        "reached through an SSH tunnel for everything except approvals. Slack ChatOps "
        "(KAV-55, ADR-0026) connects outbound instead — no inbound endpoint is ever opened "
        "for it either; `scripts/ops/approve.py` remains the fallback. Cognito JWTs would "
        "arrive only if the deferred mobile app (Phase 9) is ever built."
    ),
    openapi_tags=[
        {"name": "health", "description": "Is the stack wired: database migrated, model pulled"},
        {"name": "signals", "description": "Raw observations, exactly as collected"},
        {"name": "incidents", "description": "Correlated problems and everything done about them"},
        {"name": "actions", "description": "Approve or deny one proposed action"},
    ],
)
app.include_router(router)


class Check(BaseModel):
    ok: bool
    detail: str


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    version: str  # which gateway build answered; the component version (ADR-0013)
    postgres: Check
    ollama: Check


# A health check reports failures, it doesn't raise them. Only the exception's type goes
# into `detail`: its message can carry a connection string, and /healthz is unauthenticated.


def check_postgres() -> Check:
    try:
        with get_engine().connect() as conn:
            query = text("SELECT version_num FROM alembic_version")
            revision: str = conn.execute(query).scalar_one()
    except Exception as exc:
        return Check(ok=False, detail=type(exc).__name__)
    return Check(ok=True, detail=f"migrated to {revision}")


def check_ollama() -> Check:
    base = os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434")
    model = os.environ.get("LOCAL_MODEL", "")
    try:
        resp = httpx.get(f"{base}/api/tags", timeout=3.0)
        resp.raise_for_status()
        names = {m["name"] for m in resp.json().get("models", [])}
    except Exception as exc:
        return Check(ok=False, detail=type(exc).__name__)
    if model and model not in names:
        return Check(ok=False, detail=f"{model} not pulled")
    return Check(ok=True, detail=f"{model or 'no model configured'} ready")


@app.get("/healthz", response_model=Health, tags=["health"])
def healthz(response: Response) -> Health:
    pg, ol = check_postgres(), check_ollama()
    healthy = pg.ok and ol.ok
    if not healthy:
        response.status_code = 503
    status: Literal["ok", "degraded"] = "ok" if healthy else "degraded"
    return Health(status=status, version=__version__, postgres=pg, ollama=ol)
