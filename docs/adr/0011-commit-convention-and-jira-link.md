# ADR-0011 — Commit convention, enforced; Jira linked to GitHub by key, without smart-commit commands

- **Status:** Accepted
- **Date:** 2026-09-27
- **Deciders:** Roshan
- **Jira:** `KAV-25`

## Context

Two things were planned together in Phase 1: conventional commit messages, and Jira smart commits
(`KAV-12 #comment ... #time 2h #done`), so that one commit would feed the code history, the issue
tracker and, later, the release notes.

By `KAV-25` the convention was written down ([contributing](../contributing.md#commits)) and
followed by hand. Every non-merge commit but one early `style:` commit fits it. Nothing checked
it, though, and Jira couldn't see the repository at all: the development panel on `KAV-24`
showed 0 commits, 0 branches and 0 pull requests.

Checking how smart commits actually work turned up a constraint that the plan had missed.
[Atlassian's docs](https://support.atlassian.com/jira-software-cloud/docs/process-issues-with-smart-commits/)
say a `#comment`, `#time` or transition command runs **only if the commit's author email exactly
matches one Jira user's email**. Otherwise the commit is still linked, but the command is dropped,
sometimes silently.

This repo commits under the GitHub noreply address
(`<id>+roshanoommenreji@users.noreply.github.com`). The Jira account uses a personal Gmail address.
They don't match.

## Decision

1. **Keep the noreply commit email.** The repo goes public at v1, and every commit carries its
   author email for good. A personal address in public history gets harvested by scrapers and
   can't be removed without rewriting history.
2. **Link Jira to GitHub with the GitHub for Jira app** (Atlassian's own, free), installed on
   the `kaval` repository only. Linking works by the `KAV-<n>` key in a branch name, commit
   message or PR title, with no email match needed. Each issue then shows its commits, branch,
   pull request and CI result.
3. **No smart-commit commands.** They wouldn't run, and a `#done` that looks as if it moved the
   issue but didn't is worse than no command. Status changes stay with
   `scripts/tracking/jira-sync.py transition`, which is already step 8 of the Definition of Done.
4. **Enforce the commit convention in two places, from one rule set:**
   `scripts/dev/check_commits.py`.
   - **Local:** a `commit-msg` hook, installed by `scripts/dev/install-hooks.sh`, refuses a bad
     message before the commit exists.
   - **CI:** a step in the lint job checks every commit a pull request adds, so a clone without
     the hook can't slip one through.

   The rules:
   - the subject starts with `feat`, `fix`, `docs`, `infra` or `chore`, then `: `;
   - it is at most 100 characters;
   - on a story branch it carries a KAV key;
   - there are no smart-commit commands;
   - there are no `fixup!` commits in a PR.

   Merge and revert commits are generated, so they're exempt.

## Alternatives considered

| Option | Why not |
|---|---|
| **Commit with the Gmail address in this repo** | Makes the `#` commands work. But the address is then public in every commit from v1 on, and GitHub's "block pushes that expose my email" setting would refuse those pushes. The commands' main benefit, moving the issue, is already covered by `jira-sync.py` |
| **Change the Jira account's email to the noreply address** | A noreply address can't receive mail, so Jira's notifications and its sign-in emails would go nowhere |
| **commitlint (Node) or the pre-commit framework** | Standard in industry, but they add a Node or Python toolchain to a hook that has to work on a fresh clone before `make sync`. Five rules fit in about 100 lines of stdlib Python, tested like the rest of the code |
| **A PR-title check only (squash merges)** | This repo merges with a merge commit (`--merge`), so every branch commit lands on `main` and each one needs to be right, not just the title |
| **The hook alone** | A hook lives in `.git/hooks`, isn't versioned, and is skipped by any clone that never ran `install-hooks.sh`. CI is the check that can't be skipped |

## Consequences

- Every issue shows its code, without anyone linking it by hand. That's the part of smart commits
  that matters for an audit trail: which commits delivered this story, and did CI pass on them.
- Two surfaces carry status, and they stay separate. Git and GitHub say *what changed*;
  `jira-sync.py` says *what state the work is in*. A merge doesn't move a Jira issue by itself.
  Automating "PR merged → In Staging" belongs to Phase 4, when a merge actually deploys to
  staging (Jira automation or `release.yml`).
- The Phase 4 change records take their "linked issues" from the KAV keys in the commits since
  the last tag, which the check guarantees are there. They no longer depend on smart commits.
- A third-party app now reads the repository. It's Atlassian's own app, scoped to this one
  repository, and it can be removed in GitHub → Settings → Applications at any time.
- Semantic versioning moves to Phase 4. A version only means something once there's a release to
  tag, and `release.yml` will derive it from these same commit types.

## Revisit when

- The repo gets a second contributor. A committer whose email matches their Jira account could
  use the commands, and the check's rule 4 would need to allow that.
- Atlassian lets smart commits match a user by linked GitHub account instead of by email.
