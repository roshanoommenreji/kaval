#!/usr/bin/env python3
"""Keep the KAV Jira project in step with the repo (Definition of Done item 8).

Usage:
    python scripts/tracking/jira-sync.py show KAV-6
    python scripts/tracking/jira-sync.py tick KAV-21                 # acceptance criteria done
    python scripts/tracking/jira-sync.py transition KAV-21 Done
    python scripts/tracking/jira-sync.py create --epic KAV-6 --summary "..." \\
        --points 3 --labels infra agent --service gateway --context "..." \\
        --ac "first criterion" --ac "second" [--uat "Given ..., when ..., then ..."]
    python scripts/tracking/jira-sync.py edit KAV-22 --summary "..." --context "..." --ac "..."
    python scripts/tracking/jira-sync.py uat KAV-40 pass --env staging --note "..."
    python scripts/tracking/jira-sync.py uat KAV-40 fail --env staging --note "what broke"
    python scripts/tracking/jira-sync.py backfill-service [--apply]
    python scripts/tracking/jira-sync.py release 0.1.0 [--apply]   # Jira Release "Kaval 0.1.0"

Stories are created in the same layout every existing KAV story uses: Context, an
Acceptance Criteria checklist, UAT scenarios when the story needs acceptance (ADR-0012),
and the Definition of Done quoted from CLAUDE.md. The layout lives in jira_adf.py.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import urllib.parse
from datetime import date

import jira_adf as adf
import jira_release
from atlassian import BASE, ROOT, call

PROJECT = "KAV"
BOARD = 1
STORY_POINTS_FIELD = "customfield_10016"
SERVICES = ("gateway", "collector", "agent", "executor", "mobile", "inference", "shared", "infra")
UAT_ENVS = ("dev", "staging", "jira")  # jira: the thing accepted is a Jira artefact itself
DOD = (
    "Definition of Done (CLAUDE.md): code merged & CI green, lab doc reproducible, ADR if a "
    "decision was made, journal entry appended, cost impact noted, "
    "docs/architecture/architecture.toml updated if wired, staging verified before prod, `uat` "
    "stories signed off before prod, Jira reflects reality, dashboard/Confluence regenerated, "
    "documentation swept for stale facts."
)


def fail(msg: str, status: int, body: dict) -> None:
    print(f"{msg} (HTTP {status}): {body}", file=sys.stderr)
    sys.exit(1)


def search(jql: str, fields: str) -> list[dict]:
    out: list[dict] = []
    token = ""
    while True:
        q = f"/rest/api/3/search/jql?jql={urllib.parse.quote(jql)}&fields={fields}&maxResults=100"
        status, body = call("GET", q + (f"&nextPageToken={token}" if token else ""))
        if status != 200:
            fail(f"search failed: {jql}", status, body)
        out += body.get("issues", [])
        token = body.get("nextPageToken", "")
        if not token:
            return out


def field_ids(issuetype: str) -> dict[str, str]:
    """Field name -> id for one work type. Team-managed projects give each work type its own
    copy of a custom field (three "Service" fields for Story, Task and Bug), so look it up per
    type, never hardcode it."""
    status, meta = call("GET", f"/rest/api/3/issue/createmeta/{PROJECT}/issuetypes")
    if status != 200:
        fail("could not read work types", status, meta)
    types = {t["name"]: t["id"] for t in meta.get("issueTypes", meta.get("values", []))}
    if issuetype not in types:
        sys.exit(f"no work type {issuetype!r} in {PROJECT}; have {sorted(types)}")
    path = f"/rest/api/3/issue/createmeta/{PROJECT}/issuetypes/{types[issuetype]}"
    status, meta = call("GET", path + "?maxResults=200")
    if status != 200:
        fail(f"could not read {issuetype} fields", status, meta)
    return {f["name"]: f["fieldId"] for f in meta.get("fields", meta.get("values", []))}


def me() -> dict:
    status, body = call("GET", "/rest/api/3/myself")
    if status != 200:
        fail("could not read the current user", status, body)
    return body


def show(epic: str) -> None:
    issues = search(f"project={PROJECT} AND parent={epic} ORDER BY key", "summary,status")
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


def _read(key: str, fields: str) -> dict:
    status, body = call("GET", f"/rest/api/3/issue/{key}?fields={fields}")
    if status != 200:
        fail(f"could not read {key}", status, body)
    return dict(body["fields"])


def _write_description(key: str, doc: dict) -> None:
    status, body = call("PUT", f"/rest/api/3/issue/{key}", {"fields": {"description": doc}})
    if status != 204:
        fail(f"updating {key} failed", status, body)


def tick(key: str, section: str = adf.AC) -> None:
    """Mark one section's checklist done: the acceptance criteria unless told otherwise."""
    doc = _read(key, "description")["description"]
    ticked = adf.tick(doc, section)
    _write_description(key, doc)
    print(f"{key}: ticked {ticked} under {section!r}")


def _service_value(services: list[str]) -> list[dict[str, str]]:
    return [{"value": s} for s in services]


def create(args: argparse.Namespace) -> None:
    # Assigned to whoever runs the script. Unassigned stories showed up as a quarter of the
    # project's work under "Unassigned" on the Summary page (found 2026-09-27).
    labels = list(args.labels) + (["uat"] if args.uat and "uat" not in args.labels else [])
    fields: dict[str, object] = {
        "project": {"key": PROJECT},
        "assignee": {"accountId": me()["accountId"]},
        "issuetype": {"name": "Story"},
        "parent": {"key": args.epic},
        "summary": args.summary,
        "description": adf.description(args.context, args.ac, DOD, args.uat),
        "labels": labels,
        STORY_POINTS_FIELD: args.points,
    }
    if args.service:
        fields[field_ids("Story")["Service"]] = _service_value(args.service)
    status, body = call("POST", "/rest/api/3/issue", {"fields": fields})
    if status != 201:
        fail("create failed", status, body)
    print(f"created {body['key']}  {BASE}/browse/{body['key']}")


def edit(args: argparse.Namespace) -> None:
    """Rewrite a story whose scope changed, so the board never describes the old plan.
    Replaces summary, Context and the checklists (all unticked) in the same layout."""
    doc = adf.description(args.context, args.ac, DOD, args.uat)
    fields: dict[str, object] = {"description": doc}
    if args.summary:
        fields["summary"] = args.summary
    if args.uat:
        labels = _read(args.key, "labels")["labels"]
        if "uat" not in labels:
            fields["labels"] = labels + ["uat"]
    if args.service:
        issuetype = _read(args.key, "issuetype")["issuetype"]["name"]
        fields[field_ids(issuetype)["Service"]] = _service_value(args.service)
    status, body = call("PUT", f"/rest/api/3/issue/{args.key}", {"fields": fields})
    if status != 204:
        fail(f"editing {args.key} failed", status, body)
    extra = f" and {len(args.uat)} UAT scenarios" if args.uat else ""
    print(f"{args.key}: rewritten with {len(args.ac)} acceptance criteria{extra}")


def _comment(key: str, message: str) -> None:
    body = {"body": {"type": "doc", "version": 1,
                     "content": [{"type": "paragraph", "content": adf.text(message)}]}}
    status, resp = call("POST", f"/rest/api/3/issue/{key}/comment", body)
    if status != 201:
        fail(f"commenting on {key} failed", status, resp)


def _active_sprint() -> int | None:
    status, body = call("GET", f"/rest/agile/1.0/board/{BOARD}/sprint?state=active")
    values = body.get("values", []) if status == 200 else []
    return int(values[0]["id"]) if values else None


def uat(args: argparse.Namespace) -> None:
    """Record a UAT verdict on a story (ADR-0012). The verdict is written down, never implied:
    who accepted it, where, when, and what they saw."""
    story_fields = field_ids("Story")
    service_id = story_fields.get("Service", "")
    f = _read(args.key, f"summary,labels,description,parent,status,{service_id}")
    if "uat" not in f["labels"]:
        sys.exit(f"{args.key} isn't labelled `uat`: it needs no acceptance (ADR-0012)")
    scenarios = adf.items(f["description"], adf.UAT)
    if not scenarios:
        sys.exit(f"{args.key} has no UAT scenarios to accept; add them with `edit --uat`")
    # UAT happens in the UAT environment, so the story must be there (ADR-0012: In Staging).
    if f["status"]["name"] != "In Staging":
        sys.exit(f"{args.key} is {f['status']['name']!r}; UAT verdicts are given In Staging")
    who, today = me()["displayName"], date.today().isoformat()

    if args.verdict == "pass":
        open_defects = [
            linked["key"]
            for link in _read(args.key, "issuelinks")["issuelinks"]
            for linked in [link.get("inwardIssue") or link.get("outwardIssue")]
            if linked and linked["fields"]["issuetype"]["name"] == "Bug"
            and linked["fields"]["status"]["statusCategory"]["key"] != "done"
            and "uat-defect" in _read(linked["key"], "labels")["labels"]
        ]
        if open_defects:
            sys.exit(f"{args.key} can't pass UAT while its defects are open: {open_defects}")
        doc = f["description"]
        adf.tick(doc, adf.UAT)
        _write_description(args.key, doc)
        _comment(args.key, f"UAT passed on {args.env} by {who}, {today}: {args.note} "
                           f"({len(scenarios)} scenario(s) accepted)")
        transition(args.key, "Ready for Prod")
        return

    # Fail: the story goes back to work, and the defect gets its own trackable issue.
    _comment(args.key, f"UAT failed on {args.env} by {who}, {today}: {args.note}")
    bug_fields = field_ids("Bug")
    fields: dict[str, object] = {
        "project": {"key": PROJECT},
        "issuetype": {"name": "Bug"},
        "assignee": {"accountId": me()["accountId"]},
        "summary": f"UAT: {f['summary'][:120]} — {args.note[:80]}",
        "description": {"type": "doc", "version": 1, "content": [
            {"type": "paragraph", "content": adf.text(
                f"Found in UAT of {args.key} on {args.env}, {today}. {args.note}")}]},
        "labels": ["uat-defect"],
    }
    if f.get("parent"):
        fields["parent"] = {"key": f["parent"]["key"]}
    if service_id and f.get(service_id) and "Service" in bug_fields:
        fields[bug_fields["Service"]] = [{"value": v["value"]} for v in f[service_id]]
    if "Found in" in bug_fields and args.env in ("dev", "staging"):
        fields[bug_fields["Found in"]] = {"value": args.env}
    elif "Found in" not in bug_fields:
        print("  note: no 'Found in' field on Bug yet; skipped", file=sys.stderr)
    status, bug = call("POST", "/rest/api/3/issue", {"fields": fields})
    if status != 201:
        fail("creating the UAT bug failed", status, bug)
    call("POST", "/rest/api/3/issueLink", {"type": {"name": "Relates"},
                                            "inwardIssue": {"key": bug["key"]},
                                            "outwardIssue": {"key": args.key}})
    sprint = _active_sprint()
    if sprint:
        call("POST", f"/rest/agile/1.0/sprint/{sprint}/issue", {"issues": [bug["key"]]})
    print(f"raised {bug['key']}  {BASE}/browse/{bug['key']}")
    transition(args.key, "In Progress")


def _commits_by_key() -> dict[str, set[str]]:
    """KAV key -> every file changed by a commit whose message names it."""
    log = subprocess.run(
        ["git", "log", "--no-merges", "--name-only", "--format=%x1e%B%x1f"],
        cwd=ROOT, check=True, capture_output=True, text=True, encoding="utf-8",
    ).stdout
    out: dict[str, set[str]] = {}
    for record in log.split("\x1e"):
        message, _, files = record.partition("\x1f")
        paths = {p.strip() for p in files.splitlines() if p.strip()}
        for key in set(re.findall(r"\bKAV-\d+\b", message)):
            out.setdefault(key, set()).update(paths)
    return out


def backfill_service(apply: bool) -> None:
    """Set Service on existing issues from the folders their commits touched. Only fills an
    empty field, so a human's choice is never overwritten."""
    by_key = _commits_by_key()
    ids = {t: field_ids(t).get("Service", "") for t in ("Story", "Task", "Bug")}
    issues = search(f"project={PROJECT} AND issuetype in (Story, Task, Bug) ORDER BY key",
                    "summary,issuetype," + ",".join(i for i in ids.values() if i))
    for issue in issues:
        key, f = issue["key"], issue["fields"]
        fid = ids.get(f["issuetype"]["name"], "")
        current = [v["value"] for v in (f.get(fid) or [])] if fid else []
        derived = adf.services_for(by_key.get(key, set()))
        if current or not derived or not fid:
            note = f"already {current}" if current else "no service code" if key in by_key \
                else "no commits"
            print(f"  {key:<7} skip   ({note})")
            continue
        print(f"  {key:<7} {'set' if apply else 'would set'} {derived}")
        if apply:
            status, body = call("PUT", f"/rest/api/3/issue/{key}",
                                {"fields": {fid: _service_value(derived)}})
            if status != 204:
                fail(f"setting Service on {key} failed", status, body)
    if not apply:
        print("dry run: nothing written. Re-run with --apply.")


def release(version: str, apply: bool) -> None:
    """Make the Jira Release (fix version) `Kaval X.Y.Z` for one product version and put every
    issue its change record names into it (ADR-0013, 6). The change record is the source:
    docs/releases/<date>-vX.Y.Z.md. Safe to run twice: it adds what is missing and nothing else."""
    try:
        record = jira_release.parse(
            jira_release.find_record(ROOT / "docs" / "releases", version)
            .read_text(encoding="utf-8"))
    except (ValueError, FileNotFoundError) as e:
        sys.exit(str(e))
    status, project = call("GET", f"/rest/api/3/project/{PROJECT}")
    if status != 200:
        fail("could not read the project", status, project)
    status, existing = call("GET", f"/rest/api/3/project/{PROJECT}/versions")
    if status != 200:
        fail("could not read the project's releases", status, existing)
    have = next((v for v in existing if v["name"] == record.name), None)
    state = "already exists" if have else "creating" if apply else "would be created"
    issues = ", ".join(record.issues) or "none"
    print(f"{record.name}: {state} (released {record.date}); "
          f"{len(record.issues)} issue(s): {issues}")
    if not apply:
        print("dry run: nothing written. Re-run with --apply.")
        return
    if not have:
        status, body = call("POST", "/rest/api/3/version", {
            "name": record.name, "description": record.description, "released": True,
            "releaseDate": record.date, "projectId": int(project["id"])})
        if status != 201:
            fail(f"creating {record.name} failed", status, body)
        print(f"  created {record.name}")
    for key in record.issues:
        status, body = call("GET", f"/rest/api/3/issue/{key}?fields=fixVersions")
        if status != 200:
            print(f"  {key:<7} skip   (not readable in Jira: HTTP {status})")
            continue
        if any(v["name"] == record.name for v in body["fields"].get("fixVersions", [])):
            print(f"  {key:<7} skip   (already in {record.name})")
            continue
        status, body = call("PUT", f"/rest/api/3/issue/{key}",
                            {"update": {"fixVersions": [{"add": {"name": record.name}}]}})
        if status != 204:
            fail(f"adding {key} to {record.name} failed", status, body)
        print(f"  {key:<7} added")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_show = sub.add_parser("show", help="list an epic's stories and their status")
    p_show.add_argument("epic")

    p_tr = sub.add_parser("transition", help="move an issue to a status, e.g. Done")
    p_tr.add_argument("key")
    p_tr.add_argument("status")

    p_tk = sub.add_parser("tick", help="mark a story's acceptance criteria done")
    p_tk.add_argument("key")
    p_tk.add_argument("--section", choices=("ac", "uat"), default="ac",
                      help="uat ticks the UAT scenarios instead; normally `uat pass` does that")

    def story_args(p: argparse.ArgumentParser, *, new: bool) -> None:
        p.add_argument("--summary", required=new)
        p.add_argument("--context", required=True)
        p.add_argument("--ac", action="append", required=True, help="repeat per criterion")
        p.add_argument("--uat", action="append", default=[],
                       help="a UAT scenario (Given/When/Then); repeat; adds the uat label")
        p.add_argument("--service", action="append", choices=SERVICES, default=[],
                       help="a service the story changes; repeat")

    p_cr = sub.add_parser("create", help="create a story under an epic")
    p_cr.add_argument("--epic", required=True)
    p_cr.add_argument("--points", type=float, required=True)
    p_cr.add_argument("--labels", nargs="+", default=[])
    story_args(p_cr, new=True)

    p_ed = sub.add_parser("edit", help="rewrite a story's summary, context and checklists")
    p_ed.add_argument("key")
    story_args(p_ed, new=False)

    p_uat = sub.add_parser("uat", help="record a UAT verdict on a story (ADR-0012)")
    p_uat.add_argument("key")
    p_uat.add_argument("verdict", choices=("pass", "fail"))
    p_uat.add_argument("--env", choices=UAT_ENVS, required=True, help="where it was accepted")
    p_uat.add_argument("--note", required=True, help="what was checked, or what broke")

    p_bf = sub.add_parser("backfill-service", help="set Service from the code each issue changed")
    p_bf.add_argument("--apply", action="store_true", help="write; without it, a dry run")

    p_rel = sub.add_parser("release", help="make a product version's Jira Release (ADR-0013, 6)")
    p_rel.add_argument("version", help="product version, e.g. 0.1.0; its change record is read")
    p_rel.add_argument("--apply", action="store_true", help="write; without it, a dry run")

    args = parser.parse_args()
    if args.command == "show":
        show(args.epic)
    elif args.command == "transition":
        transition(args.key, args.status)
    elif args.command == "tick":
        tick(args.key, adf.UAT if args.section == "uat" else adf.AC)
    elif args.command == "edit":
        edit(args)
    elif args.command == "uat":
        uat(args)
    elif args.command == "backfill-service":
        backfill_service(args.apply)
    elif args.command == "release":
        release(args.version, args.apply)
    else:
        create(args)


if __name__ == "__main__":
    main()
