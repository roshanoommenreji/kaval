"""The gateway app: `/healthz` (KAV-22) plus the read-only /v1 REST surface (KAV-23).

OpenAPI is generated from the code: interactive docs at /docs, the schema at /openapi.json.
`/healthz` stays outside /v1 because probes aren't part of the versioned API contract.

The health check answers the two questions that matter before anything else can work: is the
database reachable *and migrated*, and is the local model actually pulled, not merely is
Ollama up.
"""

from __future__ import annotations

import os
from typing import Literal

import httpx
from fastapi import FastAPI, Response
from kaval_shared.db import get_engine
from pydantic import BaseModel
from sqlalchemy import text

from kaval_gateway.api import router

app = FastAPI(
    title="Kaval gateway",
    version="0.1.0",
    summary="The mobile-facing API of an ops agent that acts only with human approval.",
    description=(
        "Read-only in Phase 1: signals as collected, and incidents with their full timeline "
        "(proposals, actions, decisions, executions, outcomes). No authentication yet: the "
        "server binds to 127.0.0.1 and is reached through an SSH tunnel. Cognito JWTs "
        "arrive with the mobile app (Phase 5)."
    ),
    openapi_tags=[
        {"name": "health", "description": "Is the stack wired: database migrated, model pulled"},
        {"name": "signals", "description": "Raw observations, exactly as collected"},
        {"name": "incidents", "description": "Correlated problems and everything done about them"},
    ],
)
app.include_router(router)


class Check(BaseModel):
    ok: bool
    detail: str


class Health(BaseModel):
    status: Literal["ok", "degraded"]
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
    return Health(status="ok" if healthy else "degraded", postgres=pg, ollama=ol)
