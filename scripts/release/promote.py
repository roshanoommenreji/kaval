"""The promotion gate: may this tag go to prod, and the edit that sends it (KAV-63, ADR-0032).

ADR-0004 says nothing reaches prod without passing staging. KAV-62 made "passed staging" a fact in
Git (deploy/promotion/passed-staging.json). This is what asks that question, three ways:

    verify --tag T   Is T on the record, do the digests still match ECR, is T newer than what prod
                     runs, and is every `uat` story in the release signed off in Jira? (ADR-0012)
    pin --tag T      Re-check the record, then point prod's two files at T. Edits files only.
    body --tag T     The text of the promotion pull request (record, digests, the verify output).
    guard --base S   For a pull request: if prod's image tags changed against commit S, the new tag
                     must be on the record as it stood on S. Catches a hand edit that skips verify.

promote.yml runs `verify` in one job and `pin` in the next. CI runs `guard` on every pull request.

    python scripts/release/promote.py verify --tag sha-2459417
    python scripts/release/promote.py pin --tag sha-2459417
    python scripts/release/promote.py guard --base origin/main

Exit 0 passes, 1 refuses, 2 cannot decide (no AWS, no Jira, commit missing). A gate that cannot
decide refuses: it never says yes by default.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from base64 import b64encode
from pathlib import Path
from typing import Any

from pin_staging import TAG_LINE, pin
from staging_smoke import DIGEST_RE, RECORD, SERVICES, TAG_RE, Check

ROOT = Path(__file__).resolve().parents[2]

# The only files that say what prod runs. promote.yml is the only thing meant to write them;
# `guard` is what makes that true of everyone else.
PROD_FILES = (
    "deploy/gitops/prod/helmrelease.yaml",
    "deploy/environments/prod/values.yaml",
)
EXPECTED_PER_FILE = len(SERVICES)

# A `uat` story is accepted when it has reached one of these (ADR-0012: Ready for Prod is the
# sign-off; Done is after prod). Anything earlier means nobody has accepted it yet.
UAT_SIGNED_OFF = ("Ready for Prod", "Done")
NL, NN = "\n", "\n\n"
KEY_RE = re.compile(r"\bKAV-\d+\b")


# ── pure parts: judging a record, a release and a pull request ────────────────────────


def find_pass(record_text: str | None, tag: str) -> dict[str, Any] | None:
    for entry in json.loads(record_text or '{"passes": []}').get("passes", []):
        if entry.get("tag") == tag:
            found: dict[str, Any] = entry
            return found
    return None


def check_record(record_text: str | None, tag: str) -> list[Check]:
    """Is `tag` on the record, with a well-formed digest for every service prod runs?"""
    entry = find_pass(record_text, tag)
    if entry is None:
        return [Check("tag is on the passed-staging record", False, f"{tag} never passed staging")]
    images = entry.get("images", {})
    bad = [s for s in SERVICES if not DIGEST_RE.match(str(images.get(s, "")))]
    if bad:
        return [Check("tag is on the passed-staging record", False, f"no valid digest for {bad}")]
    return [
        Check(
            "tag is on the passed-staging record",
            True,
            f"{tag} passed {entry.get('passed_at', '?')} (commit {str(entry.get('commit'))[:7]})",
        )
    ]


def check_ecr(entry: dict[str, Any], ecr: dict[str, str]) -> Check:
    """The digests ECR holds now must be the digests that passed. A tag deleted and pushed again
    would still match by name; this is what catches it."""
    wrong = [
        f"{s}: record {entry['images'][s][:19]}..., ECR {ecr[s][:19]}..."
        for s in SERVICES
        if entry["images"][s] != ecr[s]
    ]
    if wrong:
        return Check("ECR digests equal the digests that passed", False, "; ".join(wrong))
    return Check("ECR digests equal the digests that passed", True, "all four match")


def release_keys(subjects: str) -> list[str]:
    """The stories in a release: every KAV key in the subject of a commit it adds. check_commits.py
    makes a story branch's commits carry the key, and merge commits carry the branch name."""
    keys = set(KEY_RE.findall(subjects))
    return sorted(keys, key=lambda k: int(k.split("-")[1]))


def check_uat(keys: list[str], issues: dict[str, dict[str, Any] | None]) -> Check:
    """`issues` maps a key to {labels, status}, or None when Jira has no such issue."""
    name = "every uat story in the release is signed off"
    if not keys:
        return Check(name, True, "no KAV stories in the release's commit subjects")
    uat = {k: i for k in keys if (i := issues.get(k)) and "uat" in i["labels"]}
    blockers = [f"{k} ({i['status']})" for k, i in uat.items() if i["status"] not in UAT_SIGNED_OFF]
    if blockers:
        return Check(name, False, "not signed off: " + ", ".join(blockers))
    note = f"{len(keys)} stories, {len(uat)} with uat, all accepted"
    unknown = [k for k in keys if issues.get(k) is None]
    if unknown:
        note += f"; no such issue in Jira, ignored: {', '.join(unknown)}"
    return Check(name, True, note)


def prod_tags(text: str) -> list[str]:
    return [m.group(0).split(":", 1)[1].strip() for m in TAG_LINE.finditer(text)]


def check_direction(prod_tag: str, tag: str, is_ancestor: bool) -> Check:
    """Promoting is moving prod forward. The same tag is nothing to do; an older commit is a
    rollback, which is a different act with its own workflow."""
    name = "tag is newer than what prod runs"
    if tag == prod_tag:
        return Check(name, False, f"prod already runs {tag}")
    if is_ancestor:
        return Check(name, False, f"{tag} is older than prod's {prod_tag}: that is a rollback")
    return Check(name, True, f"prod runs {prod_tag}; {tag} is ahead of it")


def pr_body(entry: dict[str, Any], prod_tag: str, evidence: str) -> str:
    """The text of the promotion pull request: what moves, the proof, and what merging means."""
    tag = entry["tag"]
    rows = NL.join(f"| `kaval/{s}:{tag}` | `{entry['images'][s]}` |" for s in SERVICES)
    fence = "`" * 3
    return (
        f"Promotes `{tag}` (commit {entry.get('commit')}) to prod. Prod runs `{prod_tag}` now.{NN}"
        f"| image | digest that passed staging |{NL}|---|---|{NL}{rows}{NN}"
        f"Passed staging {entry.get('passed_at')}. What the gate checked just now:{NN}"
        f"{fence}{NL}{evidence.strip()}{NL}{fence}{NN}"
        "**Merging this pull request is the go/no-go.** Prod is a Flux-managed cluster: once "
        "this is on `main`, the next time prod is up it pulls these images and the migration job "
        "runs against the production database. Nothing is applied by this pull request itself. "
        "CI's first run on a bot's pull request waits for a person to approve it (Actions tab, "
        f"`Approve and run`); merge when every check is green.{NN}"
        "What the staging pass did not cover is listed in `deploy/promotion/passed-staging.json` "
        "under `not_covered`."
    )


def guard(
    base: dict[str, str | None],
    head: dict[str, str | None],
    base_record: str | None,
    history: frozenset[str] = frozenset(),
) -> list[Check]:
    """A pull request may change prod's image tags only to a tag that was already on the record
    on the branch it merges into. Reading the record from the base, not from the pull request,
    means a change cannot add its own proof: the record entry has to be merged first.

    `history` is every tag prod's files pinned at an earlier commit on the base branch. Those
    tags were merged through review and prod has run them, so going back to one is allowed even
    if it predates the record (rollback.yml, ADR-0034). It is read from the base too."""
    base_tags = {t for f in PROD_FILES for t in prod_tags(base.get(f) or "")}
    head_tags = {t for f in PROD_FILES for t in prod_tags(head.get(f) or "")}
    if base_tags == head_tags:
        return [Check("prod image tags", True, f"unchanged ({', '.join(sorted(head_tags))})")]

    checks: list[Check] = []
    counts = {f: len(prod_tags(head.get(f) or "")) for f in PROD_FILES}
    if len(head_tags) != 1 or any(c != EXPECTED_PER_FILE for c in counts.values()):
        return [
            Check(
                "prod runs one release",
                False,
                f"prod's files pin {sorted(head_tags) or 'no tag'} ({counts}); want one tag on "
                f"{EXPECTED_PER_FILE} lines in each file",
            )
        ]
    (tag,) = head_tags
    checks.append(Check("prod runs one release", True, tag))
    found = check_record(base_record, tag)
    if not found[0].ok and tag in history:
        found[0] = Check(
            found[0].name,
            True,
            f"{tag} is not on the record, but prod has run it before (a rollback, ADR-0034)",
        )
    elif not found[0].ok:
        found[0] = Check(
            found[0].name,
            False,
            f"prod's tags changed to {tag}, which is not on the record on the base branch and "
            "prod has never run it: run promote.yml (or merge the record entry first)",
        )
    return checks + found


# ── the parts that touch Git, AWS and Jira ────────────────────────────────────────────


def git(*args: str, root: Path = ROOT) -> str | None:
    out = subprocess.run(
        ["git", *args], capture_output=True, text=True, encoding="utf-8", cwd=root, check=False
    )
    return out.stdout if out.returncode == 0 else None


def git_show(rev: str, path: str, root: Path = ROOT) -> str | None:
    return git("show", f"{rev}:{path}", root=root)


def ecr_digests(tag: str) -> dict[str, str]:
    exe = shutil.which("aws") or "aws"
    found: dict[str, str] = {}
    for svc in SERVICES:
        out = subprocess.run(
            [
                exe, "ecr", "describe-images", "--repository-name", f"kaval/{svc}",
                "--image-ids", f"imageTag={tag}",
                "--query", "imageDetails[0].imageDigest", "--output", "text",
            ],  # fmt: skip
            capture_output=True, text=True, encoding="utf-8", check=False,
        )  # fmt: skip
        digest = out.stdout.strip()
        if out.returncode != 0 or not DIGEST_RE.match(digest):
            raise RuntimeError(
                f"kaval/{svc}:{tag} is not readable in ECR: {out.stderr.strip()[:200]}"
            )
        found[svc] = digest
    return found


def _env_file() -> dict[str, str]:
    path = ROOT / ".env"
    if not path.exists():
        return {}
    return {
        k: v
        for k, _, v in (
            line.partition("=")
            for line in path.read_text(encoding="utf-8").splitlines()
            if "=" in line and not line.lstrip().startswith("#")
        )
    }


def jira_issue(key: str) -> dict[str, Any] | None:
    """Labels and status of one issue, read-only. None when Jira has no such issue.
    Credentials come from the environment (Actions secrets) or the git-ignored .env."""
    env = {**_env_file(), **os.environ}
    site, email, token = (env.get(k) for k in ("JIRA_SITE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"))
    if not (site and email and token):
        raise RuntimeError("JIRA_SITE_URL, JIRA_EMAIL and JIRA_API_TOKEN are not all set")
    auth = b64encode(f"{email}:{token}".encode()).decode()
    req = urllib.request.Request(
        f"{site.rstrip('/')}/rest/api/3/issue/{key}?fields=labels,status",
        headers={"Authorization": f"Basic {auth}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            fields = json.loads(resp.read())["fields"]
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise RuntimeError(f"Jira answered {exc.code} for {key}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"cannot reach Jira: {exc}") from exc
    return {"labels": fields.get("labels", []), "status": fields["status"]["name"]}


def prod_history_tags(rev: str, root: Path = ROOT) -> frozenset[str]:
    """Every tag prod's two files pinned at any commit up to `rev`: what prod has run before."""
    commits = (git("log", "--format=%H", rev, "--", *PROD_FILES, root=root) or "").split()
    return frozenset(
        t for c in commits for f in PROD_FILES for t in prod_tags(git_show(c, f, root) or "")
    )


def commit_of(tag: str) -> str | None:
    out = git("rev-parse", "--verify", f"{tag[4:]}^{{commit}}")
    return out.strip() if out else None


def current_prod_tag(root: Path = ROOT) -> str:
    tags = {t for f in PROD_FILES for t in prod_tags((root / f).read_text(encoding="utf-8"))}
    if len(tags) != 1:
        raise RuntimeError(f"prod's files pin {sorted(tags) or 'no tag'}, expected exactly one")
    return tags.pop()


# ── commands ──────────────────────────────────────────────────────────────────────────


def _report(checks: list[Check]) -> bool:
    for c in checks:
        print(f"  {'PASS' if c.ok else 'FAIL'}  {c.name}: {c.detail}")
    return all(c.ok for c in checks)


def cmd_verify(args: argparse.Namespace) -> int:
    try:
        record = (args.root / RECORD).read_text(encoding="utf-8")
        checks = check_record(record, args.tag)
        if not checks[0].ok:
            _report(checks)
            return 1
        entry = find_pass(record, args.tag) or {}

        prod_tag = current_prod_tag(args.root)
        prod_commit, new_commit = commit_of(prod_tag), entry.get("commit")
        if (
            not prod_commit
            or not new_commit
            or git("cat-file", "-e", f"{new_commit}^{{commit}}") is None
        ):
            raise RuntimeError(
                "prod's commit or the tag's commit is not in this clone (fetch full history)"
            )
        ancestor = (
            subprocess.run(
                ["git", "merge-base", "--is-ancestor", new_commit, prod_commit],
                cwd=ROOT,
                check=False,
            ).returncode
            == 0
        )
        checks.append(check_direction(prod_tag, args.tag, ancestor))
        checks.append(check_ecr(entry, ecr_digests(args.tag)))

        keys = release_keys(git("log", "--format=%s", f"{prod_commit}..{new_commit}") or "")
        checks.append(check_uat(keys, {k: jira_issue(k) for k in keys}))
        print(f"release {prod_tag} -> {args.tag}: stories {', '.join(keys) or 'none'}")
    except (RuntimeError, OSError) as exc:
        print(f"cannot decide: {exc}", file=sys.stderr)
        return 2

    ok = _report(checks)
    print(f"\n{args.tag} {'may' if ok else 'may NOT'} be promoted to prod.")
    return 0 if ok else 1


def cmd_pin(args: argparse.Namespace) -> int:
    if not TAG_RE.match(args.tag):
        print(f"not a release tag: {args.tag!r}", file=sys.stderr)
        return 1
    record = (args.root / RECORD).read_text(encoding="utf-8")
    checks = check_record(record, args.tag)
    if not _report(checks):
        return 1
    return rewrite_prod(args.root, args.tag)


def rewrite_prod(root: Path, tag: str) -> int:
    """Point both of prod's files at `tag`, or write nothing: a half-pinned prod is worse than a
    refused pin. Shared with rollback.py, which decides *whether* before calling this."""
    rewritten: dict[Path, str] = {}
    for name in PROD_FILES:
        new, count = pin((root / name).read_text(encoding="utf-8"), tag)
        if count != EXPECTED_PER_FILE:
            print(
                f"{name}: found {count} image tags, expected {EXPECTED_PER_FILE}", file=sys.stderr
            )
            return 1
        rewritten[root / name] = new
    for path, new in rewritten.items():
        path.write_text(new, encoding="utf-8", newline="")
        print(f"pinned {path.relative_to(root).as_posix()} to {tag}")
    return 0


def cmd_guard(args: argparse.Namespace) -> int:
    head = {f: (args.root / f).read_text(encoding="utf-8") for f in PROD_FILES}
    base = {f: git_show(args.base, f, args.root) for f in PROD_FILES}
    history = prod_history_tags(args.base, args.root)
    checks = guard(base, head, git_show(args.base, RECORD, args.root), history)
    ok = _report(checks)
    return 0 if ok else 1


def cmd_body(args: argparse.Namespace) -> int:
    record = (args.root / RECORD).read_text(encoding="utf-8")
    entry = find_pass(record, args.tag)
    if entry is None:
        print(f"{args.tag} is not on the record", file=sys.stderr)
        return 1
    evidence = args.evidence.read_text(encoding="utf-8") if args.evidence else "(not attached)"
    print(pr_body(entry, current_prod_tag(args.root), evidence))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, fn in (("verify", cmd_verify), ("pin", cmd_pin)):
        p = sub.add_parser(name)
        p.add_argument("--tag", required=True, help="e.g. sha-1a2b3c4")
        p.set_defaults(fn=fn)
    p = sub.add_parser("body", help="markdown for the promotion pull request")
    p.add_argument("--tag", required=True)
    p.add_argument("--evidence", type=Path, help="the verify output to quote")
    p.set_defaults(fn=cmd_body)
    p = sub.add_parser("guard")
    p.add_argument("--base", required=True, help="the commit the change merges into")
    p.set_defaults(fn=cmd_guard)
    args = parser.parse_args(argv)

    os.environ.setdefault("AWS_REGION", "ap-south-1")
    os.environ.setdefault("PYTHONUTF8", "1")
    rc: int = args.fn(args)
    return rc


if __name__ == "__main__":
    sys.exit(main())
