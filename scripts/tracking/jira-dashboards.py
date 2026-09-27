#!/usr/bin/env python3
"""Build the Kaval Jira dashboards from scripts/tracking/jira-dashboards.toml (KAV-36).

    python scripts/tracking/jira-dashboards.py          # or: make jira-dashboards

Idempotent: filters and dashboards are matched by name; a dashboard's gadgets are rebuilt only
when their layout differs from the TOML, and a gadget's settings are written only when they
differ. So a second run reports everything unchanged.

Gadget settings live in each dashboard item's properties, one per setting, as the Jira UI
stores them. isConfigured=true is set on every gadget, so it renders instead of asking to be
configured.
"""
from __future__ import annotations

import sys
import tomllib
import urllib.parse
from typing import Any

from atlassian import BASE, ROOT, call

CONFIG = ROOT / "scripts" / "tracking" / "jira-dashboards.toml"
PROJECT = "KAV"


def ok(status: int, body: Any, what: str, expect: tuple[int, ...] = (200, 201, 204)) -> Any:
    if status not in expect:
        sys.exit(f"{what} failed (HTTP {status}): {body}")
    return body


def service_field() -> str:
    """The Story work type's Service field id: the axis of the by-service tables."""
    _, meta = call("GET", f"/rest/api/3/issue/createmeta/{PROJECT}/issuetypes")
    types = {t["name"]: t["id"] for t in meta.get("issueTypes", meta.get("values", []))}
    _, meta = call("GET", f"/rest/api/3/issue/createmeta/{PROJECT}/issuetypes/{types['Story']}"
                          "?maxResults=200")
    for f in meta.get("fields", meta.get("values", [])):
        if f["name"] == "Service":
            return str(f["fieldId"])
    sys.exit("no Service field on Story; add it first (docs/labs/lab-08)")


def sync_filters(filters: list[dict[str, str]]) -> dict[str, str]:
    """Create or update each filter; returns key -> id."""
    ids: dict[str, str] = {}
    for f in filters:
        q = urllib.parse.quote(f["name"])
        path = f"/rest/api/3/filter/search?filterName={q}&expand=jql"
        body = ok(*call("GET", path), "filter search")
        match = next((x for x in body.get("values", []) if x["name"] == f["name"]), None)
        if match is None:
            made = ok(*call("POST", "/rest/api/3/filter", {"name": f["name"], "jql": f["jql"]}),
                      f"create filter {f['name']}")
            ids[f["key"]] = str(made["id"])
            print(f"  filter     created   {f['name']}")
        elif match.get("jql") != f["jql"]:
            spec = {"name": f["name"], "jql": f["jql"]}
            ok(*call("PUT", f"/rest/api/3/filter/{match['id']}", spec), "update filter")
            ids[f["key"]] = str(match["id"])
            print(f"  filter     updated   {f['name']}")
        else:
            ids[f["key"]] = str(match["id"])
            print(f"  filter     unchanged {f['name']}")
    return ids


def resolve(prefs: dict[str, str], filters: dict[str, str], service: str) -> dict[str, str]:
    out = {"isConfigured": "true"}
    for k, v in prefs.items():
        if v == "@service":
            v = service
        elif v.startswith("@"):
            v = f"filter-{filters[v[1:]]}"
        out[k] = v
    return out


def sync_dashboard(d: dict[str, Any], catalogue: dict[str, str], filters: dict[str, str],
                   service: str) -> str:
    q = urllib.parse.quote(d["name"])
    found = ok(*call("GET", f"/rest/api/3/dashboard/search?dashboardName={q}"), "dashboard search")
    match = next((x for x in found.get("values", []) if x["name"] == d["name"]), None)
    if match is None:
        match = ok(*call("POST", "/rest/api/3/dashboard", {
            "name": d["name"], "description": d["description"],
            "sharePermissions": [], "editPermissions": []}), f"create {d['name']}")
        print(f"  dashboard  created   {d['name']}")
    else:
        print(f"  dashboard  exists    {d['name']}")
    did = str(match["id"])

    want = [(g["title"], catalogue[g["type"]], g["column"], g["row"]) for g in d["gadget"]]
    have_raw = ok(*call("GET", f"/rest/api/3/dashboard/{did}/gadget"), "list gadgets")["gadgets"]
    have = sorted((g.get("title", ""), g.get("uri", ""), g["position"]["column"],
                   g["position"]["row"]) for g in have_raw)
    if have != sorted(want):
        for g in have_raw:
            ok(*call("DELETE", f"/rest/api/3/dashboard/{did}/gadget/{g['id']}"), "remove gadget")
        for g in d["gadget"]:
            ok(*call("POST", f"/rest/api/3/dashboard/{did}/gadget", {
                "uri": catalogue[g["type"]], "title": g["title"], "color": "blue",
                "position": {"column": g["column"], "row": g["row"]}}), f"add {g['title']}")
        print(f"    gadgets  rebuilt   ({len(d['gadget'])})")
        have_raw = ok(*call("GET", f"/rest/api/3/dashboard/{did}/gadget"),
                      "list gadgets")["gadgets"]
    else:
        print(f"    gadgets  unchanged ({len(have_raw)})")

    by_pos = {(g["position"]["column"], g["position"]["row"]): g for g in have_raw}
    for g in d["gadget"]:
        item = by_pos[(g["column"], g["row"])]["id"]
        base = f"/rest/api/3/dashboard/{did}/items/{item}/properties"
        changed = 0
        for key, value in resolve(g.get("prefs", {}), filters, service).items():
            status, cur = call("GET", f"{base}/{key}")
            if status == 200 and cur.get("value") == value:
                continue
            # A property's body is the bare JSON value (a string here), not an object.
            ok(*call("PUT", f"{base}/{key}", value), f"set {key} on {g['title']}")  # type: ignore[arg-type]
            changed += 1
        note = f"set {changed} setting(s)" if changed else "settings unchanged"
        print(f"    {note:<20} {g['title']}")
    return f"{BASE}/jira/dashboards/{did}"


def main() -> None:
    cfg = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    listed = ok(*call("GET", "/rest/api/3/dashboard/gadgets"), "gadget catalogue")["gadgets"]
    catalogue = {g["title"]: g.get("uri") or g.get("moduleKey") for g in listed}
    missing = {g["type"] for d in cfg["dashboard"] for g in d["gadget"]} - catalogue.keys()
    if missing:
        sys.exit(f"gadgets not in this site's catalogue: {sorted(missing)}")
    filters = sync_filters(cfg["filter"])
    service = service_field()
    for d in cfg["dashboard"]:
        url = sync_dashboard(d, catalogue, filters, service)
        print(f"    {url}")


if __name__ == "__main__":
    main()
