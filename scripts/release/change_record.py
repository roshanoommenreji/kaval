"""The change record of a production release, generated and never typed (KAV-67, ADR-0035).

promote.yml calls this when it opens the pull request that pins prod, so the record travels in the
same pull request as the pin: merging the go/no-go also records what was decided. It reads only
what is already in Git, the passed-staging record and the gate's own output, so it needs neither
AWS nor Jira and can run in the job that has neither.

    render --tag sha-2459417 --evidence evidence/verify.txt --actor roshan
    render --tag sha-2459417 --previous sha-ffb436b --date 2026-10-09   # backfill a merged one

Writes docs/releases/YYYY-MM-DD-vX.Y.Z.md and prints its path. The heading and the bullet fields
under it keep the shape docs/releases/README.md documents, because the dashboard reads them.

Exit 0 wrote it, 1 refused (tag not on the record), 2 could not decide (history missing).
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import rollback
import versions
from promote import KEY_RE, NL, NN, ROOT, commit_of, committed_prod_tag, find_pass, git
from staging_smoke import RECORD
from versions import COMPONENTS, Commit

RELEASES = "docs/releases"
# The last time going back was timed, on staging because prod was parked (ADR-0034). Update it,
# and the ADR, when prod's own time is measured.
MEASURED = "about 80 seconds from merging the rollback pull request to every service healthy"
UAT_RE = re.compile(r"every uat story in the release is signed off: (.+)")
TYPE_ORDER = ("feat", "fix", "perf", "refactor", "docs", "test", "build", "ci", "chore", "infra")


@dataclass(frozen=True)
class Inputs:
    entry: dict[str, Any]  # the passed-staging record's entry for the tag
    prev_tag: str  # what prod runs now
    prev_entry: dict[str, Any] | None
    new: tuple[dict[str, str], str]  # component versions, product version
    prev: tuple[dict[str, str], str]
    commits: list[Commit]
    migrations: list[str]  # migration files the release adds
    unreleased: str  # what the build carries beyond its tagged versions
    lead_seconds: float | None
    evidence: str
    actor: str
    date: str
    note: str = ""


# ── pure parts ────────────────────────────────────────────────────────────────────────


def risk(commits: list[Commit]) -> tuple[str, list[str]]:
    """Derived from what the diff touches, not from a mood (docs/releases/README.md).
    High: it can change the database, break a caller, or act on the cluster or the safety rules.
    Medium: infrastructure, shared code or dependencies. Low: everything else."""
    files = {f for c in commits for f in c.files}
    high, medium = [], []
    if any(f.startswith("migrations/versions/") for f in files):
        high.append("adds a database migration, so going back needs the database step")
    if any(versions.level_of(c.subject, c.body) == "major" for c in commits):
        high.append("contains a breaking change")
    if any(f.startswith("services/executor/") for f in files):
        high.append("changes the executor, the only component that acts on the cluster")
    if any(f.startswith("policy/") for f in files):
        high.append("changes the safety rules in policy/")
    if any(f.startswith(("infra/", "deploy/charts/", "deploy/gitops/")) for f in files):
        medium.append("changes infrastructure or the deployment chart")
    if any(f.startswith("services/shared/") for f in files):
        medium.append("changes code every service shares")
    if files & {"pyproject.toml", "uv.lock"}:
        medium.append("changes dependencies")
    if high:
        return "high", high + medium
    if medium:
        return "medium", medium
    return "low", ["touches no database, safety rule, executor or infrastructure file"]


def jira_keys(commits: list[Commit]) -> list[str]:
    keys = {k for c in commits for k in KEY_RE.findall(c.subject)}
    return sorted(keys, key=lambda k: int(k.split("-")[1]))


def uat_line(evidence: str) -> str:
    m = UAT_RE.search(evidence)
    return m[1].strip() if m else "not recorded (no gate output attached)"


def lead_time(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    return f"{seconds / 86400:.1f} days" if seconds >= 86400 else f"{seconds / 3600:.1f} hours"


def short_digest(digest: str | None) -> str:
    return digest if digest else "—"


def _sort_key(c: Commit) -> tuple[int, str]:
    m = versions.SUBJECT_RE.match(c.subject)
    kind = m["type"] if m else ""
    return (TYPE_ORDER.index(kind) if kind in TYPE_ORDER else len(TYPE_ORDER), c.subject)


def changes(commits: list[Commit]) -> str:
    """The commits since the last release, grouped by the component they changed."""
    groups: dict[str, list[Commit]] = {}
    for c in commits:
        owners = sorted({k for f in c.files for k in versions.components_of(f)}) or ["other"]
        for owner in owners:
            groups.setdefault(owner, []).append(c)
    if not groups:
        return "No commits since the previous release."
    out = []
    for name in [*COMPONENTS, "other"]:
        if name in groups:
            label = name if name != "other" else "no component (docs, infrastructure, CI)"
            out.append(
                f"**{label}**{NN}"
                + NL.join(
                    f"- {c.subject} (`{c.sha[:7]}`)" for c in sorted(groups[name], key=_sort_key)
                )
            )
    return NN.join(out)


def component_rows(i: Inputs) -> str:
    rows = []
    for comp in COMPONENTS:
        now, was = i.new[0][comp], i.prev[0].get(comp)
        digest = i.entry["images"].get(comp) or i.entry.get("published_not_exercised", {}).get(comp)
        prev_digest = (i.prev_entry or {}).get("images", {}).get(comp)
        if was is None:
            note = " (new)"
        elif digest and digest == prev_digest:
            note = " (unchanged)"
        else:
            note = ""
        rows.append(f"| {comp} | {now} | {was or '—'} | {short_digest(digest)}{note} |")
    return NL.join(rows)


def checks_table(entry: dict[str, Any]) -> str:
    rows = [f"| {c['name']} | {c['detail']} |" for c in entry.get("checks", [])]
    return f"| check | result |{NL}|---|---|{NL}" + NL.join(rows)


def rollback_plan(i: Inputs) -> str:
    cmd = f'gh workflow run rollback.yml --ref main -f tag={i.prev_tag} -f reason="<why>"'
    if i.migrations:
        names = ", ".join(f"`{Path(m).name}`" for m in i.migrations)
        db = (
            f"**Going back crosses a database migration** ({names}). Undo it first with the "
            "newer image, with a snapshot, or the older version's database step stops the "
            "rollback (runbook `docs/runbooks/rollback-prod.md`, step 1), then tick "
            "`accept_migrations`."
        )
    else:
        db = "Going back crosses no database migration."
    return (
        f"Prod runs `{i.prev_tag}` today, so that is the version to go back to.{NN}"
        f"```{NL}{cmd}{NL}```{NN}{db}{NN}"
        f"Last measured: {MEASURED} (staging, 2026-10-09, ADR-0034). Prod's own time has not "
        "been measured. Runbook: `docs/runbooks/rollback-prod.md`."
    )


def render(i: Inputs) -> str:
    tag, commit = i.entry["tag"], str(i.entry.get("commit", ""))
    comps, product = i.new
    level, reasons = risk(i.commits)
    keys = jira_keys(i.commits)
    gateway = i.entry["images"].get("gateway", "")
    fence = "`" * 3
    head = (
        f"# Release v{product} — {i.date}{NN}"
        f"{versions.bill_of_materials(comps, product)}{NN}"
        f"- **Environment:** prod{NL}"
        f"- **Approved by:** {i.actor}{NL}"
        f"- **Digest:** {gateway}{NL}"
        f"- **Image tag:** {tag}{NL}"
        f"- **Commit:** {commit}{NL}"
        f"- **Lead time:** {lead_time(i.lead_seconds)}{NL}"
        f"- **Rolled back:** no (as of when this record was written; rollback.yml does not edit "
        f"records yet){NL}"
        f"- **Time to restore:** —{NL}"
        f"- **UAT:** {uat_line(i.evidence)}{NN}"
        "*Generated by `scripts/release/change_record.py` when the promotion pull request was "
        'opened. "Approved by" is who started the promotion; the approval itself is merging '
        "the pull request.*" + (f"{NN}*{i.note}*" if i.note else "")
    )
    return (
        f"{head}{NN}"
        f"## Components{NN}"
        f"| Component | Version | Previous | Image digest |{NL}|---|---|---|---|{NL}"
        f"{component_rows(i)}{NN}"
        f"Previous = what prod ran before (`{i.prev_tag}`). The backup image is published but "
        f"not deployed, so it is listed and not promoted.{NN}"
        f"Unreleased changes in this build: {i.unreleased}{NN}"
        f"## Changes{NN}"
        f"{len(i.commits)} commit(s) since `{i.prev_tag}`, grouped by component.{NN}"
        f"{changes(i.commits)}{NN}"
        f"## Issues{NN}"
        + (", ".join(keys) if keys else "None: no commit in this release names a Jira key.")
        + f"{NN}## Risk{NN}"
        f"**{level}**{NN}" + NL.join(f"- {r}" for r in reasons) + NN + f"## Staging evidence{NN}"
        f"`{tag}` passed staging {i.entry.get('passed_at', '?')}.{NN}"
        f"{checks_table(i.entry)}{NN}"
        "Not covered by that pass:"
        + NN
        + NL.join(f"- {n}" for n in i.entry.get("not_covered", []))
        + NN
        + f"What the gate checked when the promotion was started:{NN}"
        f"{fence}{NL}{i.evidence.strip() or '(not attached)'}{NL}{fence}{NN}"
        f"## Rollback plan{NN}{rollback_plan(i)}{NN}"
        f"## Verification{NN}"
        "Not recorded yet: this file is written before anything has run. After the deploy, "
        f"`kubectl get pods -n kaval-prod` should show every pod `1/1 Running` on `{tag}`, and "
        f"`kubectl get helmrelease -n flux-system` should say `Released=True`.{NL}"
    )


def file_name(date: str, product: str, existing: set[str]) -> str:
    """`YYYY-MM-DD-vX.Y.Z.md`; a second record the same day for the same version gets `-2`."""
    base = f"{date}-v{product}"
    name, n = f"{base}.md", 2
    while name in existing:
        name, n = f"{base}-{n}.md", n + 1
    return name


# ── the parts that touch Git ──────────────────────────────────────────────────────────


def unreleased_note(commit: str, root: Path) -> str:
    """Changes in the build that no version covers yet, so the version number is not mistaken for
    a promise the images do not keep."""
    try:
        plan = versions.load_plan(commit, root)
    except RuntimeError:
        return "unknown (the baseline version tags do not exist yet)"
    if plan.product_next is None:
        return "none; the images carry exactly the tagged versions"
    moved = ", ".join(f"{c} would be {v}" for c, v in plan.next.items())
    return (
        f"**yes** ({moved}). The version numbers above are the last tagged ones; run "
        "release-prepare.yml before promoting if you want them to match."
    )


def oldest_commit_age(older: str, newer: str, now: datetime, root: Path) -> float | None:
    out = git("log", "--no-merges", "--format=%cI", f"{older}..{newer}", root=root) or ""
    stamps = [s for s in out.split() if s]
    if not stamps:
        return None
    return (now - datetime.fromisoformat(stamps[-1])).total_seconds()


def gather(args: argparse.Namespace, now: datetime) -> Inputs:
    root: Path = args.root
    record = (root / RECORD).read_text(encoding="utf-8")
    entry = find_pass(record, args.tag)
    if entry is None:
        raise LookupError(f"{args.tag} is not on the passed-staging record")
    commit = str(entry["commit"])
    prev_tag = args.previous or committed_prod_tag(root)
    if prev_tag == args.tag:
        raise LookupError(
            f"prod already pins {args.tag}: there is no previous version to compare with "
            "(pass --previous to write the record for a promotion that has already been merged)"
        )
    prev_commit = commit_of(prev_tag)
    if not prev_commit or git("cat-file", "-e", f"{commit}^{{commit}}", root=root) is None:
        raise RuntimeError("prod's commit or the tag's commit is not in this clone")
    new, prev = (
        versions.versions_at(commit, root),
        versions.versions_at(prev_commit, root, partial=True),
    )
    commits = versions.commits_between(prev_commit, commit, root)
    evidence = args.evidence.read_text(encoding="utf-8") if args.evidence else ""
    return Inputs(
        entry=entry,
        prev_tag=prev_tag,
        prev_entry=find_pass(record, prev_tag),
        new=new,
        prev=prev,
        commits=commits,
        migrations=rollback.migrations_added(prev_commit, commit, root),
        unreleased=unreleased_note(commit, root),
        lead_seconds=oldest_commit_age(prev_commit, commit, now, root),
        evidence=evidence,
        actor=args.actor,
        date=args.date or now.strftime("%Y-%m-%d"),
        note=args.note or "",
    )


def cmd_render(args: argparse.Namespace) -> int:
    try:
        i = gather(args, datetime.now(UTC))
    except LookupError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (RuntimeError, OSError) as exc:
        print(f"cannot decide: {exc}", file=sys.stderr)
        return 2
    out_dir = args.root / RELEASES
    out_dir.mkdir(parents=True, exist_ok=True)
    name = file_name(i.date, i.new[1], {p.name for p in out_dir.glob("*.md")})
    (out_dir / name).write_text(render(i), encoding="utf-8", newline="\n")
    print(f"{RELEASES}/{name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("render", help="write the change record for a promotion")
    p.add_argument("--tag", required=True, help="e.g. sha-2459417")
    p.add_argument("--evidence", type=Path, help="the gate's output to quote and read UAT from")
    p.add_argument("--previous", help="the tag prod ran before (default: what prod pins now)")
    p.add_argument("--actor", default="unknown", help="who started the promotion")
    p.add_argument("--note", help="one line added under the heading (for example, a backfill)")
    p.add_argument("--date", help="YYYY-MM-DD (default: today, UTC)")
    p.set_defaults(fn=cmd_render)
    args = parser.parse_args(argv)
    rc: int = args.fn(args)
    return rc


if __name__ == "__main__":
    sys.exit(main())
