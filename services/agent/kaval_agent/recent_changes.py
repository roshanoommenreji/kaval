"""Recent changes to the service a workload maps to (KAV-40, ADR-0015).

A **laptop-run** helper, like `kaval_agent.index_runbooks`: it reads this repo's own `git
log`, which the agent's Docker image never receives (the root `.dockerignore` allowlist keeps
`.git` off the daemon). It's also, honestly, a **proxy**: in Phase 2 there is no real
Kubernetes deployment history to ask — that arrives in Phase 3 — so "what changed recently"
means "what changed in this repo's code for that service", not a real deploy event. When
Phase 3's collector starts emitting real deployment signals, this module gets replaced, not
extended; see ADR-0015's Consequences.

Takes a bare **workload** name (`"checkout"`, not `"kaval-demo/checkout"` — the caller strips
the namespace, since only the workload half can ever mean a service in this repo).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from kaval_agent.index_runbooks import repo_root

# Workload -> the service folder it corresponds to, when it's one of ours. The synthetic
# scenarios also use "checkout" in a fictional demo namespace
# (kaval_collector.synthetic.DEMO_NAMESPACE); that one isn't a real service in this repo and
# deliberately doesn't map. Extend this as real services start deploying (Phase 3+).
KNOWN_SERVICES = frozenset({"gateway", "collector", "agent", "executor", "shared"})

_FIELD_SEP = "\x1f"  # a byte that will never appear in a commit subject


@dataclass(frozen=True)
class Change:
    sha: str
    at: datetime
    summary: str


@dataclass(frozen=True)
class RecentChanges:
    service: str | None  # None: workload isn't a service this repo builds
    available: bool  # False: service known, but git log couldn't be read here
    changes: list[Change]  # newest first, as `git log` already orders them


def for_workload(
    workload: str, *, since: timedelta = timedelta(days=14), now: datetime | None = None,
    root: Path | None = None,
) -> RecentChanges:
    if workload not in KNOWN_SERVICES:
        return RecentChanges(service=None, available=False, changes=[])
    cutoff = (now or datetime.now(UTC)) - since
    try:
        proc = subprocess.run(
            ["git", "log", f"--since={cutoff.isoformat()}",
             f"--pretty=format:%H{_FIELD_SEP}%aI{_FIELD_SEP}%s", "--",
             f"services/{workload}/"],
            cwd=root or repo_root(), capture_output=True, text=True, timeout=10, check=True,
        )
    except (OSError, RuntimeError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        # OSError: no git binary. RuntimeError: repo_root() found no checkout (inside the
        # Docker image). CalledProcessError: not a git repository. All the same to a caller:
        # this data source just isn't available right now.
        return RecentChanges(service=workload, available=False, changes=[])
    changes = [
        Change(sha=sha[:10], at=datetime.fromisoformat(at), summary=summary)
        for sha, at, summary in (line.split(_FIELD_SEP, 2) for line in proc.stdout.splitlines())
    ]
    return RecentChanges(service=workload, available=True, changes=changes)
