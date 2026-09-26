"""Shared Atlassian Cloud access for the Jira and Confluence scripts.

Jira and Confluence on the same site share one account and one API token, read from the
repo's gitignored `.env` (JIRA_SITE_URL, JIRA_EMAIL, JIRA_API_TOKEN). Stdlib only.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from base64 import b64encode
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            env[k] = v
    return env


ENV = load_env()
BASE = ENV["JIRA_SITE_URL"]
_AUTH = b64encode(f"{ENV['JIRA_EMAIL']}:{ENV['JIRA_API_TOKEN']}".encode()).decode()
_HEADERS = {
    "Authorization": f"Basic {_AUTH}",
    "Accept": "application/json",
    "Content-Type": "application/json",
}


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    """Returns (status, parsed body). Never raises on an HTTP error — callers check status."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers=_HEADERS, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw.decode(errors="replace")[:800]}
