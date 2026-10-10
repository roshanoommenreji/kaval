"""Read a release's change record into what Jira needs for a Release (fix version), KAV-35.

Pure text handling, no network and no credentials, so it is unit-tested (test_jira_release.py).
The record is `docs/releases/<date>-v<X.Y.Z>.md`, written by scripts/release/change_record.py
(ADR-0035). It is the one place that says which product version shipped, which component
versions were inside it, and which Jira issues its commits named.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ReleaseRecord:
    name: str  # "Kaval 0.1.0", the Jira Release's name (ADR-0013, 6)
    description: str  # "Kaval 0.1.0: gateway 0.1.0 · collector 0.1.0 ..."
    date: str  # "2026-10-09", when the promotion record was written
    issues: tuple[str, ...]  # ("KAV-32", "KAV-51", ...), sorted by number


def parse(text: str) -> ReleaseRecord:
    head = re.search(r"^# Release v(\d+\.\d+\.\d+) — (\d{4}-\d{2}-\d{2})$", text, re.M)
    if head is None:
        raise ValueError("not a change record: no '# Release vX.Y.Z — date' heading")
    version, date = head.groups()
    bom = re.search(rf"^(Kaval {re.escape(version)}: .+)$", text, re.M)
    if bom is None:
        raise ValueError(f"no 'Kaval {version}: <components>' line in the record")
    section = re.search(r"^## Issues\n+(.+?)(?:\n\n|\Z)", text, re.M | re.S)
    keys = re.findall(r"\bKAV-\d+\b", section.group(1)) if section else []
    return ReleaseRecord(
        name=f"Kaval {version}",
        description=bom.group(1),
        date=date,
        issues=tuple(sorted(set(keys), key=lambda k: int(k.split("-")[1]))),
    )


def find_record(releases_dir: Path, version: str) -> Path:
    """The record for one product version; `0.1.0` and `v0.1.0` both work."""
    version = version.removeprefix("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError(f"{version!r} is not a version like 0.1.0")
    found = sorted(releases_dir.glob(f"*-v{version}.md"))
    if not found:
        raise FileNotFoundError(f"no change record for v{version} in {releases_dir}")
    return found[-1]
