"""Shared Atlassian Cloud access for the Jira and Confluence scripts.

Jira and Confluence on the same site share one account and one API token, read from the
repo's gitignored `.env` (JIRA_SITE_URL, JIRA_EMAIL, JIRA_API_TOKEN). Stdlib only.

The request itself, with its time limit and its retry rule, is in atlassian_http.py (KAV-122).
"""
from __future__ import annotations

from base64 import b64encode
from pathlib import Path

from atlassian_http import AtlassianUnreachable, guard, send

__all__ = ["BASE", "ENV", "ROOT", "AtlassianUnreachable", "call", "guard", "load_env"]

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
    """Returns (status, parsed body). Never raises on an HTTP error — callers check status.

    If nothing answers (a time-out, a dropped connection) it raises AtlassianUnreachable, after
    three tries for a read and one for a write, which is never repeated (KAV-122). A script's
    `if __name__ == "__main__":` runs `guard(main)` so that ends it with one plain line."""
    return send(method, BASE, path, _HEADERS, body)
