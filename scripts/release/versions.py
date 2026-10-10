"""Component versions, worked out from the commits, and the tags that name them (KAV-67, ADR-0013).

Every component has its own version, written once in `services/<svc>/kaval_<svc>/__init__.py`. The
product has one too, in `pyproject.toml`. Nobody types a new number: this reads the conventional
commits that touched each component since its last tag and says what the next version is.

    plan [--markdown]   What would change, and why. Writes nothing.
    bump                Write the new versions into the files (release-prepare.yml opens the PR).
    tag --commit C      Tag every version at C that has no tag yet (release.yml, after a publish).
    show --rev R        The versions as they were at R.

The rules (ADR-0013, with the 0.x note in its 2026-10-10 amendment):
    fix -> patch, feat -> minor, `!` or a BREAKING CHANGE footer -> major (minor while the version
    is 0.x, so 1.0.0 is something a person decides). Other types change nothing.
    A change under services/shared/ and the like bumps every component that ships it; SHIPS below
    is checked against the Dockerfiles by a test, so it cannot drift from what the images hold.
    The product bumps by the largest change among its components.

Exit 0 is fine, 1 is a refusal, 2 means it could not decide (no tag to count from, no history).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

APP = ("gateway", "collector", "agent", "executor")  # the four deployed services
COMPONENTS = (*APP, "backup")  # backup is published, not deployed (ADR-0031)

# path prefix or file -> the components whose image carries it. A change there is a change to them.
SHIPS: dict[str, tuple[str, ...]] = {
    **{f"services/{svc}/": (svc,) for svc in COMPONENTS},
    "services/shared/": APP,
    "migrations/": ("gateway",),
    "policy/": ("agent", "executor"),
    "pyproject.toml": APP,
    "uv.lock": APP,
    **{
        f"scripts/ops/{name}": ("backup",)
        for name in ("backup.sh", "restore.sh", "db-roles.sql", "anonymise.sql")
    },
}

LEVELS = ("patch", "minor", "major")
SUBJECT_RE = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]*)\))?(?P<bang>!)?: ")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
INIT_LINE = re.compile(r'(?m)^__version__ = "(\d+\.\d+\.\d+)"$')
PYPROJECT_LINE = re.compile(r'(?m)^version = "(\d+\.\d+\.\d+)"$')
UV_LOCK_PROJECT = re.compile(r'(?m)^(name = "kaval"\nversion = ")(\d+\.\d+\.\d+)(")')
RS, US = "\x1e", "\x1f"  # record and field separators for git log


@dataclass(frozen=True)
class Commit:
    sha: str
    subject: str
    body: str
    files: tuple[str, ...]


def init_path(svc: str) -> str:
    return f"services/{svc}/kaval_{svc}/__init__.py"


# ── pure parts ────────────────────────────────────────────────────────────────────────


def level_of(subject: str, body: str = "") -> str | None:
    """How much a conventional commit moves a version, or None for a type that moves nothing."""
    m = SUBJECT_RE.match(subject)
    if not m:
        return None
    if m["bang"] or re.search(r"(?m)^BREAKING[ -]CHANGE:", body):
        return "major"
    return {"feat": "minor", "fix": "patch"}.get(m["type"])


def components_of(path: str) -> set[str]:
    """Which components ship this file."""
    return {c for prefix, comps in SHIPS.items() if path.startswith(prefix) for c in comps}


def largest(levels: list[str]) -> str | None:
    return max(levels, key=LEVELS.index) if levels else None


def bump(version: str, level: str) -> str:
    """The next version. While a component is 0.x a breaking change moves the minor number, so that
    1.0.0 is a decision and not an accident."""
    if not VERSION_RE.match(version):
        raise ValueError(f"not a version: {version!r}")
    major, minor, patch = (int(p) for p in version.split("."))
    if level == "major" and major == 0:
        level = "minor"
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    if level == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"unknown level: {level!r}")


def levels_by_component(commits: list[Commit]) -> dict[str, list[tuple[str, Commit]]]:
    """For each component, the commits that move it, with how far each moves it."""
    found: dict[str, list[tuple[str, Commit]]] = {}
    for c in commits:
        level = level_of(c.subject, c.body)
        if level is None:
            continue
        for comp in sorted({k for f in c.files for k in components_of(f)}):
            found.setdefault(comp, []).append((level, c))
    return found


@dataclass(frozen=True)
class Plan:
    current: dict[str, str]
    product: str
    next: dict[str, str]  # only components that change
    product_next: str | None
    why: dict[str, list[tuple[str, Commit]]]


def make_plan(current: dict[str, str], product: str, commits: dict[str, list[Commit]]) -> Plan:
    """`commits` maps each component to the commits since its last tag. The product moves by the
    biggest move among its components, so a gateway fix alone is a product patch."""
    why: dict[str, list[tuple[str, Commit]]] = {}
    nxt: dict[str, str] = {}
    for comp in COMPONENTS:
        moves = levels_by_component(commits.get(comp, [])).get(comp, [])
        level = largest([lv for lv, _ in moves])
        if level:
            nxt[comp] = bump(current[comp], level)
            why[comp] = moves
    top = largest([_effective(current[c], lv) for c, mv in why.items() for lv, _ in mv])
    return Plan(current, product, nxt, bump(product, top) if top else None, why)


def _effective(version: str, level: str) -> str:
    """The level a move really has after the 0.x rule, so the product follows the component."""
    old, new = (tuple(int(p) for p in v.split(".")) for v in (version, bump(version, level)))
    return "major" if new[0] != old[0] else "minor" if new[1] != old[1] else "patch"


def bill_of_materials(versions: dict[str, str], product: str) -> str:
    """The release's name, `Kaval 0.3.0: gateway 1.2.0 · collector 0.4.1 ...` (ADR-0013, 3)."""
    return f"Kaval {product}: " + " · ".join(f"{c} {versions[c]}" for c in COMPONENTS)


def tag_names(versions: dict[str, str], product: str) -> dict[str, str]:
    return {**{f"{c}-v{versions[c]}": c for c in COMPONENTS}, f"v{product}": "product"}


def rewrite_init(text: str, version: str) -> str:
    new, n = INIT_LINE.subn(f'__version__ = "{version}"', text)
    if n != 1:
        raise ValueError("expected exactly one __version__ line")
    return new


def rewrite_pyproject(text: str, version: str) -> str:
    new, n = PYPROJECT_LINE.subn(f'version = "{version}"', text, count=1)
    if n != 1:
        raise ValueError("no top-level version line in pyproject.toml")
    return new


def rewrite_uv_lock(text: str, version: str) -> str:
    """uv.lock records the project's own version, and `uv sync --locked` (CI) fails when it
    disagrees with pyproject.toml, so the two are bumped together."""
    new, n = UV_LOCK_PROJECT.subn(rf"\g<1>{version}\g<3>", text)
    if n != 1:
        raise ValueError('expected exactly one [[package]] name = "kaval" in uv.lock')
    return new


def markdown(plan: Plan) -> str:
    """The plan as a pull request body: what moves, from what, and the commits that moved it."""
    if plan.product_next is None:
        return "No component has a change that moves its version since its last tag."
    lines = [
        f"Kaval {plan.product} -> **{plan.product_next}**",
        "",
        "| component | from | to | because |",
        "|---|---|---|---|",
    ]
    for comp in COMPONENTS:
        if comp in plan.next:
            moves = plan.why[comp]
            top = largest([lv for lv, _ in moves]) or ""
            because = f"{top}: " + "; ".join(sorted({c.subject for _, c in moves})[:3])
            lines.append(f"| {comp} | {plan.current[comp]} | {plan.next[comp]} | {because} |")
        else:
            lines.append(f"| {comp} | {plan.current[comp]} | (unchanged) | |")
    return "\n".join(lines)


# ── the parts that touch Git ──────────────────────────────────────────────────────────


def git(*args: str, root: Path = ROOT) -> str | None:
    out = subprocess.run(
        ["git", *args], capture_output=True, text=True, encoding="utf-8", cwd=root, check=False
    )
    return out.stdout if out.returncode == 0 else None


def versions_at(
    rev: str, root: Path = ROOT, *, partial: bool = False
) -> tuple[dict[str, str], str]:
    """Component versions and the product version as written at `rev` ('' for the working tree).
    `partial` skips a component that did not exist yet at `rev` (for an older release)."""

    def read(path: str) -> str:
        text = git("show", f"{rev}:{path}", root=root) if rev else (root / path).read_text("utf-8")
        if text is None:
            raise RuntimeError(f"{path} does not exist at {rev}")
        return text

    comps = {}
    for c in COMPONENTS:
        try:
            text = read(init_path(c))
        except RuntimeError:
            if partial:
                continue
            raise
        m = INIT_LINE.search(text)
        if not m:
            raise RuntimeError(f"no __version__ in {init_path(c)} at {rev or 'the working tree'}")
        comps[c] = m[1]
    m = PYPROJECT_LINE.search(read("pyproject.toml"))
    if not m:
        raise RuntimeError("no version in pyproject.toml")
    return comps, m[1]


def commits_between(older: str, newer: str, root: Path = ROOT) -> list[Commit]:
    """Non-merge commits in `older..newer` (merge commits carry no change of their own), with the
    files each one touched."""
    fmt = f"{RS}%H{US}%s{US}%b{US}"
    span = f"{older}..{newer}"
    out = git("log", "--no-merges", "--name-only", f"--format={fmt}", span, root=root)
    if out is None:
        raise RuntimeError(f"cannot read the commits {older}..{newer} (is the history fetched?)")
    found = []
    for rec in out.split(RS)[1:]:
        sha, subject, body, files = rec.split(US, 3)
        found.append(Commit(sha, subject, body.strip(), tuple(f for f in files.split("\n") if f)))
    return found


def tag_exists(name: str, root: Path = ROOT) -> bool:
    return git("rev-parse", "--verify", "--quiet", f"refs/tags/{name}", root=root) is not None


def load_plan(rev: str = "HEAD", root: Path = ROOT) -> Plan:
    """Count each component's commits from the tag that names the version it has now."""
    current, product = versions_at(rev, root)
    commits = {}
    for c in COMPONENTS:
        tag = f"{c}-v{current[c]}"
        if not tag_exists(tag, root):
            raise RuntimeError(
                f"no tag {tag}: the version {c} has now was never tagged, so there is no point "
                "to count changes from. Tag the baseline first (`versions.py tag --commit <sha>`)"
            )
        commits[c] = commits_between(tag, rev, root)
    return make_plan(current, product, commits)


# ── commands ──────────────────────────────────────────────────────────────────────────


def cmd_plan(args: argparse.Namespace) -> int:
    try:
        plan = load_plan(root=args.root)
    except RuntimeError as exc:
        print(f"cannot decide: {exc}", file=sys.stderr)
        return 2
    if args.markdown:
        print(markdown(plan))
        return 0
    if plan.product_next is None:
        print(f"Kaval {plan.product}: nothing has changed that moves a version.")
        return 0
    print(f"Kaval {plan.product} -> {plan.product_next}")
    for comp in COMPONENTS:
        if comp in plan.next:
            n = len(plan.why[comp])
            print(f"  {comp}: {plan.current[comp]} -> {plan.next[comp]}  ({n} commit(s))")
    return 0


def cmd_bump(args: argparse.Namespace) -> int:
    try:
        plan = load_plan(root=args.root)
    except RuntimeError as exc:
        print(f"cannot decide: {exc}", file=sys.stderr)
        return 2
    if plan.product_next is None:
        print("Nothing to bump.")
        return 0
    for comp, version in plan.next.items():
        path = args.root / init_path(comp)
        path.write_text(rewrite_init(path.read_text("utf-8"), version), "utf-8", newline="")
        print(f"{init_path(comp)} -> {version}")
    pyproject = args.root / "pyproject.toml"
    new = rewrite_pyproject(pyproject.read_text("utf-8"), plan.product_next)
    pyproject.write_text(new, "utf-8", newline="")
    print(f"pyproject.toml -> {plan.product_next}")
    lock = args.root / "uv.lock"
    new_lock = rewrite_uv_lock(lock.read_text("utf-8"), plan.product_next)
    lock.write_text(new_lock, "utf-8", newline="")
    print(f"uv.lock -> {plan.product_next}")
    return 0


def cmd_tag(args: argparse.Namespace) -> int:
    """Tag, at --commit, every version written there that has no tag yet. Safe to run twice."""
    try:
        versions, product = versions_at(args.commit, args.root)
    except RuntimeError as exc:
        print(f"cannot decide: {exc}", file=sys.stderr)
        return 2
    message = bill_of_materials(versions, product)
    made = []
    for name in tag_names(versions, product):
        if tag_exists(name, args.root):
            print(f"{name}: already exists")
            continue
        out = git("tag", "-a", name, args.commit, "-m", message, root=args.root)
        if out is None:
            print(f"{name}: git could not create it", file=sys.stderr)
            return 1
        made.append(name)
        print(f"{name}: tagged {args.commit[:7]}")
    if made and args.push:
        if git("push", "origin", *made, root=args.root) is None:
            print("push failed", file=sys.stderr)
            return 1
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    try:
        versions, product = versions_at(args.rev, args.root)
    except RuntimeError as exc:
        print(f"cannot decide: {exc}", file=sys.stderr)
        return 2
    print(bill_of_materials(versions, product))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--markdown", action="store_true")
    p.set_defaults(fn=cmd_plan)
    sub.add_parser("bump").set_defaults(fn=cmd_bump)
    p = sub.add_parser("tag")
    p.add_argument("--commit", default="HEAD")
    p.add_argument("--push", action="store_true")
    p.set_defaults(fn=cmd_tag)
    p = sub.add_parser("show")
    p.add_argument("--rev", default="HEAD")
    p.set_defaults(fn=cmd_show)
    args = parser.parse_args(argv)
    rc: int = args.fn(args)
    return rc


if __name__ == "__main__":
    sys.exit(main())
