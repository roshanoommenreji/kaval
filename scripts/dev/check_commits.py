#!/usr/bin/env python3
"""Check commit messages against the repo's convention (KAV-25, docs/contributing.md).

Two callers, one rule set:

    # the commit-msg hook (scripts/dev/install-hooks.sh), on every local commit
    python scripts/dev/check_commits.py --file .git/COMMIT_EDITMSG --branch feat/KAV-25-x

    # CI (the lint job), on every commit a pull request adds
    python scripts/dev/check_commits.py --range <base-sha>..<head-sha> --branch feat/KAV-25-x

Rules:
  1. The subject is a conventional commit: `<type>[(scope)][!]: <text>`, where type is one of
     feat, fix, docs, infra, chore. Merge and revert commits are generated, so exempt.
  2. The subject is at most 100 characters.
  3. On a story branch (its name carries KAV-<n>), the subject carries a KAV key. That key is
     what links the commit to its Jira issue.
  4. No smart-commit commands (`KAV-25 #comment ...`, `#time`, `#done`). Jira runs those only
     when the commit email matches a Jira user, and this repo commits under the GitHub noreply
     address on purpose (ADR-0011), so a command would be silently ignored. Refusing it is
     better than a message that looks as if it moved the issue.
  5. In CI only: no `fixup!`/`squash!` commits. They're fine locally, but get squashed away
     (`git rebase --autosquash`) before a pull request.

Stdlib only, so the hook works before `make sync` has ever run.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

TYPES = ("feat", "fix", "docs", "infra", "chore")
SUBJECT = re.compile(rf"^(?:{'|'.join(TYPES)})(?:\([a-z0-9-]+\))?!?: \S")
MAX_SUBJECT = 100
KEY = re.compile(r"\bKAV-\d+\b")
# A key followed, on the same line, by `#` and a letter: `#comment`, `#time`, `#done`,
# `#in-progress`. `#1` (a PR number) doesn't count.
SMART_COMMAND = re.compile(r"\bKAV-\d+\b[^\n]*?(?<![\w#])#[A-Za-z]")
GENERATED = ("Merge ", 'Revert "')
AUTOSQUASH = ("fixup! ", "squash! ", "amend! ")


def problems(message: str, branch: str = "", *, in_ci: bool = False) -> list[str]:
    """Everything wrong with one commit message; empty means it passes."""
    # git drops `#` lines from an edited message; the hook sees them before it does.
    lines = [ln for ln in message.splitlines() if not ln.startswith("#")]
    subject = next((ln for ln in lines if ln.strip()), "").rstrip()
    if not subject:
        return ["the message is empty"]
    if subject.startswith(GENERATED):
        return []
    if subject.startswith(AUTOSQUASH):
        return ["squash fixup!/squash! commits before opening a pull request"] if in_ci else []

    found = []
    if not SUBJECT.match(subject):
        found.append(
            f"the subject must start with one of {', '.join(TYPES)}, then ': ' "
            f"(e.g. 'feat: KAV-25 ...'); got {subject[:60]!r}"
        )
    if len(subject) > MAX_SUBJECT:
        found.append(f"the subject is {len(subject)} characters; keep it to {MAX_SUBJECT}")
    if KEY.search(branch) and not KEY.search(subject):
        found.append(
            f"branch {branch!r} is a story branch, so the subject needs its KAV key; "
            "the key is what links the commit to the Jira issue"
        )
    if SMART_COMMAND.search("\n".join(lines)):
        found.append(
            "smart-commit commands (#comment, #time, #done ...) don't run here: commits use "
            "the GitHub noreply email, which matches no Jira user (ADR-0011). Keep the key, "
            "drop the command, and use scripts/tracking/jira-sync.py for status changes"
        )
    return found


def commits_in(rev_range: str) -> list[tuple[str, str]]:
    """(short sha, full message) for every commit in the range, oldest first."""
    out = subprocess.run(
        ["git", "log", "--reverse", "--format=%h%x00%B%x1e", rev_range],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    pairs = []
    for record in out.split("\x1e"):
        record = record.strip("\n")
        if record:
            sha, _, body = record.partition("\x00")
            pairs.append((sha, body))
    return pairs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", help="a commit message file (the commit-msg hook)")
    source.add_argument("--range", help="a git revision range, e.g. base..head (CI)")
    parser.add_argument("--branch", default="", help="the branch the commits are on")
    args = parser.parse_args()

    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            checked = [("this commit", fh.read())]
    else:
        checked = commits_in(args.range)

    failed = 0
    for sha, message in checked:
        for problem in problems(message, args.branch, in_ci=bool(args.range)):
            print(f"  {sha}: {problem}", file=sys.stderr)
            failed += 1
    if failed:
        print("\n  Commit convention: docs/contributing.md#commits", file=sys.stderr)
        return 1
    if args.range:
        print(f"{len(checked)} commit(s) follow the convention")
    return 0


if __name__ == "__main__":
    sys.exit(main())
