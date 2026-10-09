"""The rollback gate: may prod go back to this earlier tag, and the edit that sends it (KAV-66).

promote.py only moves prod forward, and its guard refuses any tag that never passed staging, so
the version prod ran before cannot be put back by it. This is the other direction, with its own
rules. A rollback is an emergency, so it asks only what it can answer from Git and ECR and it
never waits on Jira.

    verify --tag T [--accept-migrations]
                     Has prod run T before, or did T pass staging? Is T an earlier version of what
                     prod runs? Do T's four images still exist in ECR (unchanged, if recorded)? Does
                     going back cross a database migration (refused unless accepted on purpose)?
    pin --tag T      Re-check T is known, then point prod's two files at T. Edits files only.
    body --tag T --reason R
                     The text of the rollback pull request (why, the images, the evidence).

rollback.yml runs `verify` in one job and `pin` in the next. The `promotion-guard` CI job
(promote.py) allows exactly the tags that `known` accepts here: on the record, or run by prod
before (ADR-0034).

    python scripts/release/rollback.py verify --tag sha-ffb436b
    python scripts/release/rollback.py pin --tag sha-ffb436b

Exit 0 passes, 1 refuses, 2 cannot decide (no AWS, commit missing). A gate that cannot decide
refuses: it never says yes by default.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from promote import (
    NL,
    NN,
    ROOT,
    _report,
    commit_of,
    current_prod_tag,
    ecr_digests,
    find_pass,
    git,
    prod_history_tags,
    rewrite_prod,
)
from staging_smoke import RECORD, SERVICES, TAG_RE, Check

MIGRATIONS = "migrations/versions"
REASON_MAX = 300


# ── pure parts: judging a rollback ────────────────────────────────────────────────────


def check_known(record_text: str | None, history: frozenset[str], tag: str) -> Check:
    """A rollback target must be a version we already trusted: one prod has run, or one that
    passed staging. Anything else is a new release and belongs to promote.yml."""
    name = "tag is one prod has run before or one that passed staging"
    on_record = find_pass(record_text, tag) is not None
    if tag in history:
        return Check(
            name,
            True,
            f"prod has run {tag} before" + (" and it is on the record" if on_record else ""),
        )
    if on_record:
        return Check(name, True, f"{tag} passed staging (prod has not run it)")
    return Check(name, False, f"{tag} never ran in prod and never passed staging")


def check_earlier(prod_tag: str, tag: str, is_ancestor: bool) -> Check:
    """A rollback goes backwards. The same tag is nothing to do; a newer or unrelated one is a
    release, which is promote.yml's job."""
    name = "tag is an earlier version of what prod runs"
    if tag == prod_tag:
        return Check(name, False, f"prod already runs {tag}")
    if not is_ancestor:
        return Check(name, False, f"{tag} is not older than prod's {prod_tag}: use promote.yml")
    return Check(name, True, f"{tag} is an ancestor of prod's {prod_tag}")


def check_images(entry: dict[str, Any] | None, ecr: dict[str, str]) -> Check:
    """All four images must still exist. If the tag is on the record they must also be the very
    digests that passed; if it is older than the record, ECR's immutable tags are the guarantee."""
    name = "the four images exist in ECR"
    if entry is None:
        return Check(
            name, True, "all four present (older than the record, so no digest to compare)"
        )
    wrong = [s for s in SERVICES if entry["images"].get(s) != ecr.get(s)]
    if wrong:
        return Check(name, False, f"digest differs from the record for {', '.join(wrong)}")
    return Check(name, True, "all four present and equal to the recorded digests")


def check_schema(added: list[str], accepted: bool) -> Check:
    """Going back across a database migration runs older code against a newer schema. It is often
    fine (a new nullable column) and sometimes is not, and nothing here can tell which, so the
    person must say they know."""
    name = "going back crosses no database migration"
    if not added:
        return Check(name, True, "no migration was added after this version")
    listing = ", ".join(Path(a).name for a in added)
    if accepted:
        return Check(
            name, True, f"crosses {len(added)} migration(s), accepted on purpose: {listing}"
        )
    return Check(
        name,
        False,
        f"crosses {len(added)} migration(s): {listing}. The older code would run against the "
        "newer database. Re-run with accept_migrations if that is understood and acceptable",
    )


def pr_body(
    tag: str, prod_tag: str, reason: str, evidence: str, entry: dict[str, Any] | None
) -> str:
    """The text of the rollback pull request: why, what moves, the proof, what merging means."""
    fence = "`" * 3
    rows = ""
    if entry:
        rows = (
            f"| image | digest that passed staging |{NL}|---|---|{NL}"
            + NL.join(f"| `kaval/{s}:{tag}` | `{entry['images'][s]}` |" for s in SERVICES)
            + NN
        )
    return (
        f"Rolls prod back from `{prod_tag}` to `{tag}`.{NN}"
        f"**Why:** {reason.strip()[:REASON_MAX] or '(no reason given)'}{NN}"
        f"{rows}"
        f"What the gate checked just now:{NN}{fence}{NL}{evidence.strip()}{NL}{fence}{NN}"
        "**Merging this pull request is the go/no-go.** Prod is a Flux-managed cluster: once this "
        "is on `main` it pulls the older images within about a minute of being up. Nothing is "
        "applied by this pull request itself. CI's first run on a bot's pull request waits for a "
        "person to approve it (Actions tab, `Approve and run`); merge when every check is "
        f"green.{NN}"
        "A rollback puts the old code back; it does not undo database changes the newer code made."
    )


# ── the parts that touch Git, AWS ─────────────────────────────────────────────────────


def is_ancestor(older: str, newer: str, root: Path = ROOT) -> bool:
    return (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", older, newer], cwd=root, check=False
        ).returncode
        == 0
    )


def migrations_added(older: str, newer: str, root: Path = ROOT) -> list[str]:
    """Migration files that exist at `newer` and not at `older`: what going back would cross."""
    out = git("diff", "--name-only", "--diff-filter=A", older, newer, "--", MIGRATIONS, root=root)
    if out is None:
        raise RuntimeError("cannot compare the two versions' migrations (is the history fetched?)")
    return [p for p in out.split() if p.endswith(".py")]


# ── commands ──────────────────────────────────────────────────────────────────────────


def cmd_verify(args: argparse.Namespace) -> int:
    if not TAG_RE.match(args.tag):
        print(f"not a release tag: {args.tag!r}", file=sys.stderr)
        return 1
    try:
        record = (args.root / RECORD).read_text(encoding="utf-8")
        prod_tag = current_prod_tag(args.root)
        known = check_known(record, prod_history_tags("HEAD", args.root), args.tag)
        if not known.ok:
            _report([known])
            return 1

        prod_commit, target_commit = commit_of(prod_tag), commit_of(args.tag)
        if not prod_commit or not target_commit:
            raise RuntimeError(
                "prod's commit or the tag's commit is not in this clone (fetch full history)"
            )
        checks = [
            known,
            check_earlier(prod_tag, args.tag, is_ancestor(target_commit, prod_commit, args.root)),
        ]
        if not checks[-1].ok:
            _report(checks)
            return 1
        checks.append(check_images(find_pass(record, args.tag), ecr_digests(args.tag)))
        checks.append(
            check_schema(
                migrations_added(target_commit, prod_commit, args.root), args.accept_migrations
            )
        )
        print(f"rollback {prod_tag} -> {args.tag}")
    except (RuntimeError, OSError) as exc:
        print(f"cannot decide: {exc}", file=sys.stderr)
        return 2

    ok = _report(checks)
    print(f"\nprod {'may' if ok else 'may NOT'} be rolled back to {args.tag}.")
    return 0 if ok else 1


def cmd_pin(args: argparse.Namespace) -> int:
    if not TAG_RE.match(args.tag):
        print(f"not a release tag: {args.tag!r}", file=sys.stderr)
        return 1
    record = (args.root / RECORD).read_text(encoding="utf-8")
    known = check_known(record, prod_history_tags("HEAD", args.root), args.tag)
    if not _report([known]):
        return 1
    return rewrite_prod(args.root, args.tag)


def cmd_body(args: argparse.Namespace) -> int:
    record = (args.root / RECORD).read_text(encoding="utf-8")
    evidence = args.evidence.read_text(encoding="utf-8") if args.evidence else "(not attached)"
    print(
        pr_body(
            args.tag,
            current_prod_tag(args.root),
            args.reason,
            evidence,
            find_pass(record, args.tag),
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("verify")
    p.add_argument("--tag", required=True, help="e.g. sha-ffb436b")
    p.add_argument("--accept-migrations", action="store_true", help="allow crossing a migration")
    p.set_defaults(fn=cmd_verify)
    p = sub.add_parser("pin")
    p.add_argument("--tag", required=True)
    p.set_defaults(fn=cmd_pin)
    p = sub.add_parser("body", help="markdown for the rollback pull request")
    p.add_argument("--tag", required=True)
    p.add_argument("--reason", default="", help="why, in a sentence")
    p.add_argument("--evidence", type=Path, help="the verify output to quote")
    p.set_defaults(fn=cmd_body)
    args = parser.parse_args(argv)

    os.environ.setdefault("AWS_REGION", "ap-south-1")
    os.environ.setdefault("PYTHONUTF8", "1")
    rc: int = args.fn(args)
    return rc


if __name__ == "__main__":
    sys.exit(main())
