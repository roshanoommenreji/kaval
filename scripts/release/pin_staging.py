"""Point staging at a freshly published image tag (KAV-61, ADR-0030).

release.yml builds one set of images from a merged commit, tags them `sha-<short>`, and calls
this to rewrite staging's image tags. Staging deploys the way prod does: the tag is committed to
Git and Flux pulls it. This script is the only thing that edits those lines, so the edit is
mechanical and tested rather than a sed one-liner inside a workflow.

Two files hold staging's tags, kept in step by hand until now (ADR-0025, Decision 8):
the Flux HelmRelease and the environment values file CI renders. Both are rewritten together.

    python scripts/release/pin_staging.py --tag sha-1a2b3c4
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILES = (
    "deploy/gitops/staging/helmrelease.yaml",
    "deploy/environments/staging/values.yaml",
)
# gateway, agent, executor, collector. The backup image is not deployed to staging.
EXPECTED_PER_FILE = 4

TAG_VALUE = re.compile(r"^sha-[0-9a-f]{7,40}$")
TAG_LINE = re.compile(r"(?m)^([ \t]*tag:[ \t]*)sha-[0-9a-f]{7,40}(?=[ \t]*$)")


def pin(text: str, tag: str) -> tuple[str, int]:
    """Return the text with every `tag: sha-...` line set to `tag`, and how many were set."""
    if not TAG_VALUE.match(tag):
        raise ValueError(f"not a release tag: {tag!r} (want sha- and 7 to 40 hex digits)")
    return TAG_LINE.subn(lambda m: f"{m.group(1)}{tag}", text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tag", required=True, help="e.g. sha-1a2b3c4")
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root")
    args = parser.parse_args(argv)

    rewritten: dict[Path, str] = {}
    for name in FILES:
        path = args.root / name
        new, count = pin(path.read_text(encoding="utf-8"), args.tag)
        # A different count means the file's shape changed (a service added or removed). Writing
        # anyway would half-pin staging, so stop and make a person look.
        if count != EXPECTED_PER_FILE:
            print(
                f"{name}: found {count} image tags, expected {EXPECTED_PER_FILE}",
                file=sys.stderr,
            )
            return 1
        rewritten[path] = new

    for path, new in rewritten.items():
        path.write_text(new, encoding="utf-8", newline="")
        print(f"pinned {path.relative_to(args.root).as_posix()} to {args.tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
