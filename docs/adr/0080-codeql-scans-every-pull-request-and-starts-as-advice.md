# ADR-0080: CodeQL scans every pull request, and starts as advice, not a gate

**Status:** Accepted on 2026-11-07 (started on Roshan's `go`). Decision 4, when the scan becomes a required check, is a default he can change. Built and proved in `KAV-110`; it changes only CI, so it may merge at any time.
**Date:** 2026-11-07
**Related:** [ADR-0071](0071-the-public-repo-gets-a-security-policy-owners-and-a-weekly-scorecard.md) (the first Scorecard reading found this gap),
[ADR-0072](0072-a-written-threat-model-and-a-prompt-injection-set.md) (threat T-SUPPLY-5), [ADR-0010](0010-ci-pipeline-and-supply-chain.md) (pinned actions),
`KAV-110`, `KAV-111`, [Lab 81](../labs/lab-81-turning-on-codeql-and-reading-what-it-says.md)

## Context

Scorecard's first reading (2026-11-01, [Lab 71](../labs/lab-71-a-security-policy-owners-and-a-weekly-scorecard.md)) scored its code-analysis check (SAST) **0**:
"0 commits out of 30 are checked with a SAST tool". The threat model lists the same hole (T-SUPPLY-5, "no static code analysis runs").
Everything else in CI checks that the code *works* (tests, types, lint) or that its *ingredients* are safe (Trivy, gitleaks, pinned actions, hashes).
Nothing reads the code itself for the known kinds of security mistake: input that reaches a database query or a shell command, an unsafe file path, a
workflow that lets a pull request run code with the repository's token.

On 2026-11-07 GitHub's own "default setup" for code scanning was `not-configured`, and the nine open alerts in the Security tab were all Scorecard's own.
The repository is public, so CodeQL costs nothing.

## Decision

1. **`.github/workflows/codeql.yml`** runs CodeQL on every pull request (no path filter), on `main` after each merge, every Monday 04:41 UTC and by hand.
2. **Two languages:** `python` (the services, scripts, migrations and evals) and `actions` (the workflow files: CI is code, and it holds a token).
   `build-mode: none`, so nothing is built, and the default set of queries. There is no JavaScript or TypeScript in the repository today; when the mobile app
   arrives it is added to the list, and the test names the exact set so that the change is deliberate.
3. **Least power, pinned.** The token starts with nothing; the job gets `contents: read` and `security-events: write` (to put the result in the Security tab).
   Every action is pinned to a full commit SHA, `init` and `analyze` are on the same release, and the checkout keeps no credentials.
4. **Advice first, a gate later.** It is not a required check on day one. It becomes one when both of these hold: (a) seven days in a row in which every
   scan, on pull requests and on `main`, finished without a failure of CodeQL's own; and (b) every open alert has a decision (fixed, or dismissed with a
   written reason). Then Roshan adds the two checks, `codeql python (static analysis)` and `codeql actions (static analysis)`, to the branch rule: a
   GitHub setting, shown first and done only on his own go. Whether to also require GitHub's own `CodeQL` check (it fails a pull request that adds
   a serious new alert; seen going red on the throwaway pull request, "4 new alerts including 2 critical") is decided then, from the first weeks' numbers.
5. **Findings are decided, not ignored.** The ones the first scan raised were triaged in the pull request that added the workflow: fixed when small and safe,
   otherwise dismissed in the Security tab with a reason (`false positive`, `won't fix` or `used in tests`) and a sentence of why. They are listed in Lab 81.

## What real companies do, and why this one

1. **CodeQL through a workflow file** (chosen): the pattern GitHub documents for anything beyond the defaults. The configuration is in the repository, so it is
   reviewed, pinned, tested and rolled back like the rest of CI; results appear in the Security tab and on the pull request.
2. **CodeQL "default setup"** (a switch in the repository settings, no file): the quickest, and fine for a small project. Rejected because the configuration
   lives outside the repository, so it cannot be reviewed, pinned or covered by a test, and it cannot scan the workflow files with the same care.
3. **Semgrep or SonarCloud:** good tools, with more setup (an account or a token, rule packs to choose and keep) and, for some features, a price. Not ruled
   out: [future-scope.md](../future-scope.md) (FS-8) keeps Semgrep as a second opinion for rules CodeQL does not have.
4. **A gate from the first day:** what mature teams end up with, and what a new scanner should not start with. Too many alarms at once teach people to
   ignore them. Start as advice, tune, then require (the same order the Trivy exceptions in [ADR-0033](0033-dated-trivy-exception-for-the-bundled-opa-binary.md) follow).

## Consequences

- **Cost $0.** Code scanning and the hosted runners are free for a public repository.
- **More checks on every pull request**, running next to CI and not blocking it. Lab 81 records how long they take.
- **A new kind of alert to decide.** Some will be false positives. Every dismissal stays in the Security tab with its reason and can be reopened.
- **Scorecard's SAST check should read about 7 once this is on `main`, and rise to 10.** Scorecard gives 7 of the 10 points for having a CodeQL workflow
  and the other 3 for the share of the last 30 merged pull requests that ran a scanner, so about one tenth of a point for each merged pull request that
  ran it. Lab 81 records the real number after the first run on `main`.
- **Python is read as written.** `build-mode: none` is the supported way to scan Python and does not install the project's libraries. A gap shows up as a
  missed path, not a false alarm; the prompt-injection set and the evals cover the model side separately.
- **Dependabot's own pull requests are scanned too.** GitHub allows the result upload on the `pull_request` event. A merge commit *authored by Dependabot*
  (merging with Dependabot's own "squash and merge" command) would run with a read-only token and fail the upload; this repository merges through GitHub's
  merge button as Roshan, so it does not arise. Said here so that nobody is surprised if the way of merging ever changes.
- **If it becomes required, it must keep running on every pull request.** A path filter would leave a docs-only pull request waiting for a result that
  never comes; the test refuses a filter for that reason.
- **A scan of `main` is never cancelled** by a newer push (it is the one that opens and closes alerts); a scan of a pull request is, because the newer push
  replaces it.

## Testing

`scripts/dev/test_codeql_workflow.py`, nine tests: the triggers (and no filter on pull requests), a single weekly schedule, the two permissions, the two
languages with no build, one release for `init` and `analyze`, one category per language, a checkout without credentials, and a time limit. The existing
`test_repo_hygiene.py` already fails on any action that is not pinned to a full commit SHA and now covers the new file. The negative case: breaking the
workflow thirteen different ways (a path filter, a push trigger on every branch, a daily schedule, a broad token, a job that can write the code, a missing
language, a build step, fail-fast on, `init` and `analyze` on different releases, one category for both languages, a checkout that keeps credentials, no
real time limit, a moving tag) was caught every time by the test meant to catch it. The live proof was the first run on the pull request that adds it: nothing
found in 268 Python files and 8 workflow files, which proves little alone, so a throwaway pull request planted four mistakes (a query and a shell command built
from a web request, the Flask debugger left on, a workflow pasting an issue title into a shell command) and CodeQL caught all four, two critical; GitHub's
`CodeQL` check went red. One surprise is recorded in Lab 81: the planted workflow was missed while its token was empty, because CodeQL calls an injection
critical only where the job has something worth stealing.
