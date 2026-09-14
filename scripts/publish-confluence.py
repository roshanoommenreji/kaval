#!/usr/bin/env python3
"""Publish the Kaval Confluence space from the repository.

Same discipline as scripts/dashboard.py: nothing is hand-authored in Confluence
that could instead be derived. The repo stays the single source of truth for
anything technical (ADRs, runbooks); Confluence gets a human-facing layer that
links out to it, plus genuinely Confluence-native content (meeting notes) that
has no home in git.

Idempotent: rerunning updates existing pages in place rather than duplicating
them, the same way `python scripts/dashboard.py` regenerates rather than
appends.

Usage:
    python scripts/publish-confluence.py

Requires .env with JIRA_SITE_URL, JIRA_EMAIL, JIRA_API_TOKEN (same Atlassian
account and token as the Jira automation -- Confluence Cloud shares auth with
Jira Cloud on the same site).
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from base64 import b64encode
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPACE_KEY = "KAV"
SPACE_NAME = "Kaval"


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            env[k] = v
    return env


ENV = load_env()
BASE = ENV["JIRA_SITE_URL"]
AUTH = b64encode(f"{ENV['JIRA_EMAIL']}:{ENV['JIRA_API_TOKEN']}".encode()).decode()
HEADERS = {
    "Authorization": f"Basic {AUTH}",
    "Accept": "application/json",
    "Content-Type": "application/json",
}


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=HEADERS, method=method)
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


# ── minimal markdown -> Atlassian Document Format ──────────────────────────
# Deliberately hand-rolled against our own documents, same philosophy as
# dashboard.py's markdown_to_html: a focused converter is enough when the
# input is our own docs, and it keeps this script dependency-free.

def text(s: str) -> dict:
    return {"type": "text", "text": s}


def inline(s: str) -> list[dict]:
    """Very small inline parser: `code`, **bold**, [text](url)."""
    parts: list[dict] = []
    i = 0
    pattern = re.compile(r"`([^`]+)`|\*\*([^*]+)\*\*|\[([^\]]+)\]\(([^)]+)\)")
    for m in pattern.finditer(s):
        if m.start() > i:
            parts.append(text(s[i:m.start()]))
        if m.group(1) is not None:
            parts.append({"type": "text", "text": m.group(1), "marks": [{"type": "code"}]})
        elif m.group(2) is not None:
            parts.append({"type": "text", "text": m.group(2), "marks": [{"type": "strong"}]})
        elif m.group(3) is not None:
            parts.append({"type": "text", "text": m.group(3), "marks": [{"type": "link", "attrs": {"href": m.group(4)}}]})
        i = m.end()
    if i < len(s):
        parts.append(text(s[i:]))
    return parts or [text(s)]


def md_to_adf(md: str) -> list[dict]:
    lines = md.splitlines()
    content: list[dict] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if line.startswith("```"):
            lang = line[3:].strip() or None
            i += 1
            code_lines = []
            while i < len(lines) and not lines[i].startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1
            attrs = {"language": lang} if lang else {}
            content.append({"type": "codeBlock", "attrs": attrs, "content": [text("\n".join(code_lines))]})
            continue
        m = re.match(r"^(#{1,4})\s+(.*)", line)
        if m:
            level = min(len(m.group(1)) + 1, 6)  # doc has one H1 title already; shift down
            content.append({"type": "heading", "attrs": {"level": level}, "content": inline(m.group(2))})
            i += 1
            continue
        if line.startswith("> "):
            quote_lines = []
            while i < len(lines) and lines[i].startswith("> "):
                quote_lines.append(lines[i][2:])
                i += 1
            content.append({"type": "blockquote", "content": [{"type": "paragraph", "content": inline(" ".join(quote_lines))}]})
            continue
        if re.match(r"^[-*]\s+", line):
            items = []
            while i < len(lines) and re.match(r"^[-*]\s+", lines[i]):
                items.append({"type": "listItem", "content": [{"type": "paragraph", "content": inline(re.sub(r"^[-*]\s+", "", lines[i]))}]})
                i += 1
            content.append({"type": "bulletList", "content": items})
            continue
        if re.match(r"^\d+\.\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\d+\.\s+", lines[i]):
                items.append({"type": "listItem", "content": [{"type": "paragraph", "content": inline(re.sub(r"^\d+\.\s+", "", lines[i]))}]})
                i += 1
            content.append({"type": "orderedList", "content": items})
            continue
        # paragraph: gather until blank line
        para_lines = [line]
        i += 1
        while i < len(lines) and lines[i].strip() and not lines[i].startswith(("#", "```", ">", "-", "*")):
            para_lines.append(lines[i])
            i += 1
        content.append({"type": "paragraph", "content": inline(" ".join(para_lines))})
    return content


def doc(*blocks: dict | list[dict]) -> dict:
    flat: list[dict] = []
    for b in blocks:
        flat.extend(b if isinstance(b, list) else [b])
    return {"type": "doc", "version": 1, "content": flat}


def h(level: int, s: str) -> dict:
    return {"type": "heading", "attrs": {"level": level}, "content": inline(s)}


def p(s: str) -> dict:
    return {"type": "paragraph", "content": inline(s)}


def bullets(items: list[str]) -> dict:
    return {"type": "bulletList", "content": [{"type": "listItem", "content": [p(i)]} for i in items]}


# ── Confluence operations ───────────────────────────────────────────────────

def get_space_id() -> str | None:
    status, body = call("GET", f"/wiki/api/v2/spaces?keys={SPACE_KEY}")
    if status == 200 and body.get("results"):
        return body["results"][0]["id"]
    return None


def ensure_space() -> str:
    sid = get_space_id()
    if sid:
        print(f"space {SPACE_KEY} exists: {sid}")
        return sid
    status, body = call("POST", "/wiki/api/v2/spaces", {
        "name": SPACE_NAME,
        "key": SPACE_KEY,
        "description": {"value": "Kaval — autonomous ops agent. Human-facing layer over the repo and Jira.", "representation": "plain"},
    })
    if status not in (200, 201):
        print("CREATE SPACE FAILED", status, json.dumps(body)[:500])
        sys.exit(1)
    print(f"created space {SPACE_KEY}: {body['id']}")
    return body["id"]


def find_page(space_id: str, title: str) -> dict | None:
    status, body = call("GET", f"/wiki/api/v2/spaces/{space_id}/pages?title={urllib.parse.quote(title)}")
    if status == 200 and body.get("results"):
        return body["results"][0]
    return None


def upsert_page(space_id: str, title: str, adf: dict, parent_id: str | None = None) -> str:
    existing = find_page(space_id, title)
    body_payload = {"representation": "atlas_doc_format", "value": json.dumps(adf)}
    if existing:
        page_id = existing["id"]
        status, resp = call("PUT", f"/wiki/api/v2/pages/{page_id}", {
            "id": page_id, "status": "current", "title": title, "spaceId": space_id,
            "body": body_payload, "version": {"number": existing["version"]["number"] + 1, "message": "republished by publish-confluence.py"},
        })
        if status not in (200, 201):
            print(f"UPDATE FAILED {title}", status, json.dumps(resp)[:500])
        else:
            print(f"updated  : {title} ({page_id})")
        return page_id
    payload = {"spaceId": space_id, "status": "current", "title": title, "body": body_payload}
    if parent_id:
        payload["parentId"] = parent_id
    status, resp = call("POST", "/wiki/api/v2/pages", payload)
    if status not in (200, 201):
        print(f"CREATE FAILED {title}", status, json.dumps(resp)[:500])
        sys.exit(1)
    print(f"created  : {title} ({resp['id']})")
    return resp["id"]


import urllib.parse  # noqa: E402  (kept near use for readability)


def parse_adrs() -> list[tuple[str, str, str]]:
    """(id, title, status) for each docs/adr/*.md, sorted by id."""
    out = []
    for f in sorted((ROOT / "docs/adr").glob("[0-9]*.md")):
        text_ = f.read_text(encoding="utf-8")
        m_title = re.search(r"^#\s*(ADR-\d+)\s*[—-]\s*(.+)$", text_, re.M)
        m_status = re.search(r"\*\*Status:\*\*\s*(\w+)", text_)
        if m_title:
            out.append((m_title.group(1), m_title.group(2).strip(), m_status.group(1) if m_status else "?"))
    return out


def main() -> None:
    space_id = ensure_space()

    jira_url = f"{BASE}/jira/software/projects/{SPACE_KEY}/boards/1"
    dashboard_url = "https://claude.ai/code/artifact/c6ca9411-2b4b-47a8-81c1-3f3cc4c34d0c"

    # ── Project Home ────────────────────────────────────────────────────
    home = doc(
        p("Autonomous ops agent with human-in-the-loop approval. Watches a Kubernetes cluster "
          "and an AWS bill, diagnoses with a self-hosted Gemma 3 1B, and acts only after a tap "
          "on a phone."),
        h(2, "Where things live"),
        bullets([
            f"Live progress dashboard (derived from the repo, updated every session): [{dashboard_url}]({dashboard_url})",
            f"Jira board (10 epics, one per phase): [{jira_url}]({jira_url})",
            "Source code, ADRs, labs, learning material: private GitHub repo (public at v1)",
        ]),
        h(2, "How this space is organised"),
        p("This space is the human-facing layer. Anything technical -- architecture, decisions, "
          "how-to steps -- is authored in the repo and only summarised or linked here, so there is "
          "never two versions of the same fact to keep in sync."),
        bullets([
            "Requirements & Scope -- what this project is and isn't",
            "Decisions Log -- one line per ADR, links to the full record in-repo",
            "Runbooks -- published from docs/runbooks/, for troubleshooting without repo access",
            "Meeting / Session Notes -- native to this space, not duplicated anywhere",
            "Release Notes -- links to each generated release record once releases begin",
        ]),
    )
    home_id = upsert_page(space_id, "Kaval -- Project Home", home)

    # ── Requirements & Scope ────────────────────────────────────────────
    scope = doc(
        p("Full detail lives in the project plan (local file, not yet public). This page is the "
          "short version for anyone who needs the shape of it without reading the whole thing."),
        h(2, "What this is"),
        p("An autonomous operations agent that watches a live Kubernetes cluster and an AWS bill, "
          "diagnoses problems with a self-hosted LLM, proposes remediations, and executes them "
          "only after human approval from a phone."),
        h(2, "Non-negotiable constraints"),
        bullets([
            "Cost ceiling: $25/month, enforced by the system itself, not by discipline",
            "The reasoning component (agent) holds read-only credentials only; a separate scoped executor performs all mutations",
            "Nothing reaches prod without passing a real staging environment first",
            "Secrets never enter git history -- the repo goes public at v1",
        ]),
        h(2, "Scope boundary"),
        p("Phases 0-9 are the committed build (see the Jira epics). Work beyond that -- integration "
          "with real third-party systems, a multi-tenant chapter, demo craft -- is tracked as "
          "future scope in the repo, deliberately not committed to."),
    )
    upsert_page(space_id, "Requirements & Scope", scope, home_id)

    # ── Decisions Log ────────────────────────────────────────────────────
    adrs = parse_adrs()
    decision_bullets = [f"**{aid}** -- {title} ({status})" for aid, title, status in adrs]
    decisions = doc(
        p("One line per architecture decision. The full reasoning -- context, rejected "
          "alternatives, consequences -- lives in the ADR itself in the repo; this page exists so "
          "the decision history is visible without repo access."),
        h(2, "Decisions"),
        bullets(decision_bullets) if decision_bullets else p("No ADRs found."),
        h(2, "Why decisions are recorded this way"),
        p("An ADR captures not just what was decided but what was rejected and why -- the part a "
          "changelog never keeps. That's the part worth having in an interview."),
    )
    upsert_page(space_id, "Decisions Log", decisions, home_id)

    # ── Runbooks (parent + one child per file) ──────────────────────────
    runbooks_home = doc(
        p("Published from docs/runbooks/ in the repo. The repo copy is authoritative -- it is also "
          "the corpus the agent's own RAG pipeline retrieves from when diagnosing an incident, so "
          "it is never edited here directly. This page exists for troubleshooting without needing "
          "repo access."),
        h(2, "Available runbooks"),
    )
    runbook_files = sorted(f for f in (ROOT / "docs/runbooks").glob("*.md") if f.name != "README.md")
    runbooks_home["content"].append(bullets([f.stem.replace("-", " ") for f in runbook_files]) if runbook_files else p("None published yet."))
    runbooks_id = upsert_page(space_id, "Runbooks", runbooks_home, home_id)

    for f in runbook_files:
        title = f.read_text(encoding="utf-8").splitlines()[0].lstrip("# ").strip()
        body_md = "\n".join(f.read_text(encoding="utf-8").splitlines()[1:])
        adf = doc(md_to_adf(body_md))
        upsert_page(space_id, f"Runbook -- {title}", adf, runbooks_id)

    # ── Meeting / Session Notes ──────────────────────────────────────────
    notes_home = doc(
        p("Native to this space -- these notes have no equivalent in the repo. The repo's "
          "docs/journal/ covers what changed technically each session; this page is for anything "
          "that isn't code or documentation history (decisions still being talked through, things "
          "to raise next session, stakeholder-facing framing)."),
        h(2, "2026-09-14"),
        p("Jira and Confluence stood up. Jira: 10 epics, 5 Phase 0 stories, real acceptance "
          "criteria, story points, assignee -- built via the REST API rather than by hand after "
          "the first pass was correctly called out as not up to standard. Confluence: this space, "
          "published the same way -- generated from the repo rather than hand-authored, so it "
          "can't drift."),
    )
    upsert_page(space_id, "Meeting -- Session Notes", notes_home, home_id)

    # ── Release Notes ────────────────────────────────────────────────────
    releases_dir = ROOT / "docs/releases"
    release_files = sorted(releases_dir.glob("[0-9]*.md")) if releases_dir.exists() else []
    releases = doc(
        p("Each production release generates a change record in docs/releases/ automatically -- "
          "version, approver, image digests, staging evidence, rollback plan. This page links to "
          "each one as it's created."),
        h(2, "Releases"),
        bullets([f.stem for f in release_files]) if release_files else p("No releases yet -- the first arrives with the delivery pipeline in Phase 4."),
    )
    upsert_page(space_id, "Release Notes", releases, home_id)

    print("\ndone. space home:", f"{BASE}/wiki/spaces/{SPACE_KEY}/overview")


if __name__ == "__main__":
    main()
