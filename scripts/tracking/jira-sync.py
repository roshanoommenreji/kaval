#!/usr/bin/env python3
"""Keep the KAV Jira project in step with the repo (Definition of Done item 8).

Usage:
    python scripts/tracking/jira-sync.py show KAV-6
    python scripts/tracking/jira-sync.py transition KAV-21 Done
    python scripts/tracking/jira-sync.py create --epic KAV-6 --summary "..." \\
        --points 3 --labels infra agent --context "..." --ac "first criterion" --ac "second"

Stories are created in the same layout every existing KAV story uses: Context, an
Acceptance Criteria checklist, and the Definition of Done quoted from CLAUDE.md.
"""
from __future__ import annotations

import argparse
import sys
import urllib.parse
import uuid

from atlassian import BASE, call

PROJECT = "KAV"
STORY_POINTS_FIELD = "customfield_10016"
DOD = (
    "Definition of Done (CLAUDE.md): code merged & CI green, lab doc reproducible, ADR if a "
    "decision was made, journal entry appended, cost impact noted, architecture.toml updated if "
    "wired, staging verified before prod, Jira reflects reality, dashboard/Confluence regenerated."
)


def fail(msg: str, status: int, body: dict) -> None:
    print(f"{msg} (HTTP {status}): {body}", file=sys.stderr)
    sys.exit(1)


def show(epic: str) -> None:
    jql = urllib.parse.quote(f"project={PROJECT} AND parent={epic} ORDER BY key")
    status, body = call("GET", f"/rest/api/3/search/jql?jql={jql}&fields=summary,status")
    if status != 200:
        fail(f"search under {epic} failed", status, body)
    issues = body.get("issues", [])
    if not issues:
        print(f"{epic}: no child issues")
    for issue in issues:
        f = issue["fields"]
        print(f"{issue['key']:<8} {f['status']['name']:<15} {f['summary']}")


def transition(key: str, target: str) -> None:
    status, body = call("GET", f"/rest/api/3/issue/{key}/transitions")
    if status != 200:
        fail(f"could not read transitions for {key}", status, body)
    options = {t["to"]["name"].lower(): t for t in body["transitions"]}
    chosen = options.get(target.lower())
    if chosen is None:
        available = ", ".join(t["to"]["name"] for t in body["transitions"])
        print(f"{key}: no transition to '{target}'. Available: {available}", file=sys.stderr)
        sys.exit(1)
    status, body = call(
        "POST", f"/rest/api/3/issue/{key}/transitions", {"transition": {"id": chosen["id"]}}
    )
    if status != 204:
        fail(f"transition {key} -> {target} failed", status, body)
    print(f"{key} -> {chosen['to']['name']}")


def _text(value: str) -> list[dict]:
    return [{"type": "text", "text": value}]


def _description(context: str, criteria: list[str]) -> dict:
    # Atlassian rejects taskList/taskItem nodes without a localId, with a bare
    # "INVALID_INPUT" and no detail — hence the uuid on every one.
    items = [
        {
            "type": "taskItem",
            "attrs": {"localId": str(uuid.uuid4()), "state": "TODO"},
            "content": _text(c),
        }
        for c in criteria
    ]
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "heading", "attrs": {"level": 3}, "content": _text("Context")},
            {"type": "paragraph", "content": _text(context)},
            {"type": "heading", "attrs": {"level": 3}, "content": _text("Acceptance Criteria")},
            {"type": "taskList", "attrs": {"localId": str(uuid.uuid4())}, "content": items},
            {"type": "blockquote", "content": [{"type": "paragraph", "content": _text(DOD)}]},
        ],
    }


def create(args: argparse.Namespace) -> None:
    fields = {
        "project": {"key": PROJECT},
        "issuetype": {"name": "Story"},
        "parent": {"key": args.epic},
        "summary": args.summary,
        "description": _description(args.context, args.ac),
        "labels": args.labels,
        STORY_POINTS_FIELD: args.points,
    }
    status, body = call("POST", "/rest/api/3/issue", {"fields": fields})
    if status != 201:
        fail("create failed", status, body)
    print(f"created {body['key']}  {BASE}/browse/{body['key']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_show = sub.add_parser("show", help="list an epic's stories and their status")
    p_show.add_argument("epic")

    p_tr = sub.add_parser("transition", help="move an issue to a status, e.g. Done")
    p_tr.add_argument("key")
    p_tr.add_argument("status")

    p_cr = sub.add_parser("create", help="create a story under an epic")
    p_cr.add_argument("--epic", required=True)
    p_cr.add_argument("--summary", required=True)
    p_cr.add_argument("--context", required=True)
    p_cr.add_argument("--ac", action="append", required=True, help="repeat per criterion")
    p_cr.add_argument("--points", type=float, required=True)
    p_cr.add_argument("--labels", nargs="+", default=[])

    args = parser.parse_args()
    if args.command == "show":
        show(args.epic)
    elif args.command == "transition":
        transition(args.key, args.status)
    else:
        create(args)


if __name__ == "__main__":
    main()
