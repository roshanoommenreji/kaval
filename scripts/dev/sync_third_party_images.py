"""Copy the pinned third-party images from the bot's manifest into every file that names them.

KAV-170. Dependabot reads one file, deploy/third-party-images/Dockerfile.images, and opens a
pull request when an image there has a newer release. The same three images are named in six
other files (the chart, compose, CI, the database server's start-up script and two scripts
that start a throwaway database). A bot cannot read most of those, and a pull request that
moved one place and not the others would break the build or, worse, test one image and run
another. So this script is the one step between "the bot's pull request" and "green":

    python scripts/dev/sync_third_party_images.py          # rewrite every place from the manifest
    python scripts/dev/sync_third_party_images.py --check  # change nothing; exit 1 if any differs

The check is also run by scripts/dev/test_third_party_image_pins.py in CI. The script only ever
rewrites a pin (repository:tag@sha256:digest) it recognises, never anything else in a file, and
it refuses a database image that is not PostgreSQL 16 on Debian bookworm (a newer Debian changes
the text sort order and can corrupt indexes; KAV-169, ADR-0107).
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = "deploy/third-party-images/Dockerfile.images"
DIGEST = r"@sha256:[0-9a-f]{64}"


@dataclass(frozen=True)
class Image:
    stage: str  # the `AS <stage>` name in the manifest
    repository: str
    tag: str  # a regular expression for the tags this repository may be pinned to
    files: tuple[str, ...]  # every file that names the image

    @property
    def pin(self) -> re.Pattern[str]:
        return re.compile(re.escape(self.repository) + ":" + self.tag + DIGEST)


IMAGES: tuple[Image, ...] = (
    Image(
        "database",
        "pgvector/pgvector",
        r"[0-9]+(?:\.[0-9]+)*-pg16-bookworm",  # PostgreSQL 16 on Debian bookworm, nothing else
        (
            "deploy/charts/kaval/values.yaml",
            "compose.yaml",
            ".github/workflows/ci.yml",
            "infra/modules/database/user_data.sh.tftpl",
            "scripts/ops/restore-drill.sh",
            "scripts/ops/seed-refresh.sh",
        ),
    ),
    Image(
        "ollama",
        "ollama/ollama",
        r"[0-9]+(?:\.[0-9]+)*",
        ("deploy/charts/kaval/values.yaml", "compose.yaml"),
    ),
    Image(
        "prometheus",
        "quay.io/prometheus/prometheus",
        r"v[0-9]+(?:\.[0-9]+)*",
        ("deploy/charts/kaval/values.yaml",),
    ),
)

FROM_LINE = re.compile(r"^FROM\s+(?P<ref>\S+)\s+AS\s+(?P<stage>\S+)\s*$")


class SyncError(Exception):
    """The manifest or a file is not in the shape this script is willing to rewrite."""


def read_manifest(root: Path = ROOT) -> dict[str, str]:
    """The pin of every image, by stage name, checked against what each image may be."""
    path = root / MANIFEST
    if not path.is_file():
        raise SyncError(f"{MANIFEST} is missing")
    found: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = FROM_LINE.match(line)
        if match:
            if match["stage"] in found:
                raise SyncError(f"{MANIFEST} names the stage {match['stage']} twice")
            found[match["stage"]] = match["ref"]
    for image in IMAGES:
        ref = found.get(image.stage)
        if ref is None:
            raise SyncError(f"{MANIFEST} has no `FROM ... AS {image.stage}` line")
        if not image.pin.fullmatch(ref):
            need = f"{image.repository}:<tag>@sha256:<64 hex digits>"
            if image.stage == "database":
                need += ", the tag ending -pg16-bookworm (PostgreSQL 16 on Debian bookworm)"
            raise SyncError(f"refusing {ref!r} for {image.stage}: it must be {need}")
    extra = set(found) - {image.stage for image in IMAGES}
    if extra:
        raise SyncError(f"{MANIFEST} has a stage this script does not know: {sorted(extra)}")
    return found


def _text(root: Path, rel: str) -> str:
    # bytes, so a file's line endings are never touched
    return (root / rel).read_bytes().decode("utf-8")


def drift(root: Path = ROOT) -> list[str]:
    """Every place whose pin differs from the manifest, one line each. Empty when all agree."""
    pins = read_manifest(root)
    problems: list[str] = []
    for image in IMAGES:
        for rel in image.files:
            found = image.pin.findall(_text(root, rel))
            if not found:
                problems.append(f"{rel}: does not name {image.repository} pinned by digest")
            for old in sorted(set(found) - {pins[image.stage]}):
                problems.append(f"{rel}: {old}  (the manifest has {pins[image.stage]})")
    return problems


def sync(root: Path = ROOT) -> list[str]:
    """Rewrite every place to the manifest's pin. Returns the files that changed."""
    pins = read_manifest(root)
    changed: list[str] = []
    for image in IMAGES:
        new = pins[image.stage]
        for rel in image.files:
            before = _text(root, rel)
            if not image.pin.search(before):
                raise SyncError(
                    f"{rel}: does not name {image.repository} pinned by digest, "
                    "so there is nothing to move"
                )
            after = image.pin.sub(new, before)  # a pin holds no backslash
            if after != before:
                (root / rel).write_bytes(after.encode("utf-8"))
                if rel not in changed:
                    changed.append(rel)
    return changed


def main(argv: list[str], root: Path = ROOT) -> int:
    check = "--check" in argv
    try:
        if check:
            problems = drift(root)
            for line in problems:
                print(line)
            if problems:
                print(
                    f"\nThe places above disagree with {MANIFEST}.\n"
                    "Fix: python scripts/dev/sync_third_party_images.py   (then commit the result)",
                    file=sys.stderr,
                )
                return 1
            print(f"every place agrees with {MANIFEST}")
            return 0
        changed = sync(root)
    except SyncError as err:
        print(f"sync_third_party_images: {err}", file=sys.stderr)
        return 2
    print("changed: " + ", ".join(changed) if changed else "nothing to change: all places agree")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
