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

### The first result (run by hand 2026-11-01, about two minutes, success)

The report lists only checks that scored under 10, so a check that is missing passed or could not be judged. Nine findings, in six checks:

| Check | Score | Verdict |
|---|---|---|
| Code-Review | 0 | **Cannot be fixed on a one-person repository.** It counts changes approved by someone other than the author (0 of 12). Honest and expected; it is the cost of working alone. |
| Maintained | 0 | **Fixes itself.** The repository was created on 2026-09-26; the check wants 90 days of history, so it changes around 2026-12-25. |
| SAST | 0 | **Worth fixing.** No static code analysis ran on any of the last 30 commits. CodeQL is free on a public repository. Story `KAV-110`, **built 2026-11-07** ([Lab 81](lab-81-turning-on-codeql-and-reading-what-it-says.md)). |
| Pinned-Dependencies | 8 | **Worth a look.** Four Dockerfiles (gateway line 67, executor 81, collector 66, agent 98) run `pip install --no-deps .`, which installs Kaval's own code without a hash; every library before it is already hash-checked (`--require-hashes`). Story `KAV-111`. |
| Fuzzing | 0 | **Not worth it now.** Fuzzing suits parsers and libraries that take hostile input; the nearest thing here is the model-output validation, which the golden-incident evals and the planned prompt-injection set (Phase 8) cover better. |
| CII-Best-Practices | 0 | **Skipped for now.** A badge application that mostly restates what the policy, tests and ADRs already show. Revisit when the repository is announced. |

Everything else was either 10 or not judged. Branch-Protection did not appear, as expected: the default token cannot read the setting, so it is checked by hand: `main` requires eleven status checks (verified through the GitHub API on 2026-11-01), administrators can still bypass, and no approving review is required (zero), because of the one-person limit above.

No overall number is quoted here: the report in the Security tab lists findings, not the total, and a number I computed myself would be a guess.


## Done when

- [x] `SECURITY.md`, `CODEOWNERS` and the Scorecard workflow exist, and the tests pass
- [x] Private vulnerability reporting is on *(2026-11-01, `enabled: true`; community health 57 to 71)*
- [x] The Scorecard workflow ran and the result is recorded above, with every gap explained *(2026-11-01)*
- [x] GitHub recognises the security policy *(2026-11-01: the GraphQL field `isSecurityPolicyEnabled` is `true`; the REST community-profile file list did not show it, so that list is the unreliable one)*

## Cost

$0. Actions minutes and code scanning are free on a public repository.
