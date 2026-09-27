"""Pure helpers for Jira's document format (ADF) and for mapping code paths to services.

No network and no credentials, so they are unit-tested in CI (test_jira_adf.py) while
jira-sync.py, which talks to Jira, stays a thin layer over them.

A story's description has a fixed layout:

    ### Context            paragraph
    ### Acceptance Criteria checklist   <- the developer verifies these
    ### UAT scenarios      checklist   <- only on stories labelled `uat`; Roshan accepts these
    > Definition of Done

Checklists are found by the heading before them, so ticking one section never touches the other.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

Node = dict[str, Any]  # one ADF node: {"type": ..., "content": [...], "attrs": {...}}

AC = "Acceptance Criteria"
UAT = "UAT scenarios"

# Folder prefix -> value of the Jira "Service" field. First match wins, so the more specific
# prefix goes first. Paths outside these (docs, CI, tracking scripts) belong to no service.
SERVICE_PATHS: tuple[tuple[str, str], ...] = (
    ("services/gateway/", "gateway"),
    ("services/collector/", "collector"),
    ("services/agent/", "agent"),
    ("services/executor/", "executor"),
    ("services/shared/", "shared"),
    ("migrations/", "shared"),
    ("inference/", "inference"),
    ("mobile/", "mobile"),
    ("infra/", "infra"),
    ("deploy/", "infra"),
    ("compose.yaml", "infra"),
)


def text(value: str) -> list[Node]:
    return [{"type": "text", "text": value}]


def heading(value: str) -> Node:
    return {"type": "heading", "attrs": {"level": 3}, "content": text(value)}


def checklist(items: Iterable[str]) -> Node:
    # Atlassian rejects taskList/taskItem nodes without a localId, with a bare
    # "INVALID_INPUT" and no detail, hence the uuid on every one.
    return {
        "type": "taskList",
        "attrs": {"localId": str(uuid.uuid4())},
        "content": [
            {
                "type": "taskItem",
                "attrs": {"localId": str(uuid.uuid4()), "state": "TODO"},
                "content": text(item),
            }
            for item in items
        ],
    }


def description(context: str, criteria: list[str], dod: str,
                uat: list[str] | None = None) -> Node:
    content = [
        heading("Context"),
        {"type": "paragraph", "content": text(context)},
        heading(AC),
        checklist(criteria),
    ]
    if uat:
        content += [heading(UAT), checklist(uat)]
    content.append({"type": "blockquote", "content": [{"type": "paragraph", "content": text(dod)}]})
    return {"type": "doc", "version": 1, "content": content}


def _plain(node: Node) -> str:
    if node.get("type") == "text":
        return str(node.get("text", ""))
    return "".join(_plain(c) for c in node.get("content", []))


def _sections(doc: Node) -> dict[str, list[Node]]:
    """Heading text -> the taskLists that follow it, up to the next heading."""
    out: dict[str, list[Node]] = {}
    current = ""
    for node in doc.get("content", []):
        if node.get("type") == "heading":
            current = _plain(node).strip()
        elif node.get("type") == "taskList":
            out.setdefault(current, []).append(node)
    return out


def items(doc: Node, section: str) -> list[tuple[bool, str]]:
    """(done, text) for each checklist item under one heading."""
    return [
        (item.get("attrs", {}).get("state") == "DONE", _plain(item))
        for tl in _sections(doc).get(section, [])
        for item in tl.get("content", [])
        if item.get("type") == "taskItem"
    ]


def tick(doc: Node, section: str) -> int:
    """Mark every item under `section` done, in place; returns how many changed.

    Stories written before the layout had headings keep working: with no UAT heading
    anywhere, ticking the acceptance criteria ticks every checklist, as it always did."""
    sections = _sections(doc)
    if section == AC and AC not in sections and UAT not in sections:
        lists = [tl for tls in sections.values() for tl in tls]
    else:
        lists = sections.get(section, [])
    changed = 0
    for tl in lists:
        for item in tl.get("content", []):
            attrs = item.setdefault("attrs", {})
            if item.get("type") == "taskItem" and attrs.get("state") != "DONE":
                attrs["state"] = "DONE"
                changed += 1
    return changed


def services_for(paths: Iterable[str]) -> list[str]:
    """The Service values a set of changed files touches, sorted."""
    found = set()
    for path in paths:
        if path.endswith(".gitkeep"):  # an empty placeholder folder isn't a change to a service
            continue
        for prefix, service in SERVICE_PATHS:
            if path == prefix or path.startswith(prefix):
                found.add(service)
                break
    return sorted(found)
