# Lab 71 — A security policy, named owners and a weekly Scorecard

**Phase:** 8 (public-repo hygiene) · **Story:** `KAV-109` · **Time:** about 45 minutes · **Cost:** $0

A public repository should tell a stranger how to report a vulnerability privately, say who owns the risky folders, and be measured by someone else's yardstick. This lab adds all three. The decision is [ADR-0071](../adr/0071-the-public-repo-gets-a-security-policy-owners-and-a-weekly-scorecard.md).

## Prerequisites

- A public GitHub repository you administer, the `gh` command signed in, the project's Python environment (`make sync`)

## Part 1 — See what is missing

```bash
gh api repos/OWNER/REPO/community/profile --jq '{health: .health_percentage, files: (.files | keys)}'
gh api repos/OWNER/REPO/private-vulnerability-reporting
```

On 2026-11-01 this showed 57% health, no security policy, and `{"enabled": false}`.

## Part 2 — The three files

1. `.github/SECURITY.md`: how to report privately, what to expect and when, what is in scope and out of scope. No e-mail address, no account. GitHub finds the file in `.github/` and shows it on the Security tab.
2. `.github/CODEOWNERS`: `*` plus the folders where a mistake costs most. The header says why the "Require review from Code Owners" branch rule stays off for a one-person repository.
3. `.github/workflows/scorecard.yml`: weekly and by hand, token starts with nothing, the job gets `contents: read`, `actions: read`, `security-events: write`, results go to the Security tab only (`publish_results: false`). Every action is pinned to a full commit SHA. To find a SHA for a tag that is an annotated tag, ask the tag for its object, and if the type is `tag` ask once more for the commit it points to (`gh api repos/<owner>/<repo>/git/tags/<sha>`).

## Part 3 — Tests

```bash
python -m pytest scripts/dev/test_repo_hygiene.py -q
```

13 pass. To see them guard something, change `publish_results: false` to `true`, or change a pinned action to `@v4`: the matching test fails. Put it back.

## Part 4 — Switch on private reporting (after the merge, on Roshan's own go)

```bash
gh api -X PUT repos/OWNER/REPO/private-vulnerability-reporting
gh api repos/OWNER/REPO/private-vulnerability-reporting      # {"enabled": true}
```

Undo with `-X DELETE`. This is a setting on GitHub, so it is shown first and done only on a go.

## Part 5 — Read the first score (after the merge)

Run the workflow by hand: `gh workflow run scorecard.yml`, then open Security → Code scanning → filter on the "scorecard" tool. Record each check below with its score, and for every one under 10 whether it is worth fixing, cannot be fixed on a one-person repository, or is the known "unknown" (Branch-Protection).

*(To be filled in with the first real result.)*

## Done when

- [x] `SECURITY.md`, `CODEOWNERS` and the Scorecard workflow exist, and the tests pass
- [ ] Private vulnerability reporting is on (`{"enabled": true}`)
- [ ] The Scorecard workflow ran and the result is recorded above, with every gap explained
- [ ] The community profile shows a security policy

## Cost

$0. Actions minutes and code scanning are free on a public repository.
