# Lab 81 — Turning on CodeQL, and reading what it says

**Phase:** 8 (public-repo hygiene) · **Story:** `KAV-110` · **Time:** about 1 hour · **Cost:** $0

CodeQL is GitHub's code scanner: it reads the source without running it and looks for known kinds of security mistake (input that reaches a database query or a shell command, an unsafe file path, a workflow that lets a pull request run code with the repository's token). [Lab 71](lab-71-a-security-policy-owners-and-a-weekly-scorecard.md) found that nothing did this (Scorecard's SAST check scored 0) and the threat model listed the same hole (T-SUPPLY-5). This lab turns it on, shows that it stays quiet on clean code **and** shouts on bad code, and explains how to read and dismiss an alert. The decision is [ADR-0080](../adr/0080-codeql-scans-every-pull-request-and-starts-as-advice.md).

## Prerequisites

- A public GitHub repository you administer (code scanning is free for public repositories), the `gh` command signed in, the project's Python environment (`make sync`)

## Part 1 — See what is missing

```bash
gh api repos/OWNER/REPO/code-scanning/default-setup --jq .state
gh api "repos/OWNER/REPO/code-scanning/alerts?state=open&per_page=100" --jq '[.[] | .tool.name] | group_by(.) | map({tool: .[0], alerts: length})'
gh api "repos/OWNER/REPO/code-scanning/alerts?state=open&per_page=100" --jq '.[] | select(.rule.id == "SASTID") | .most_recent_instance.message.text'
```

On 2026-11-07 this showed `not-configured`; `[{"alerts":9,"tool":"Scorecard"}]` (every open alert was Scorecard's own, no code scanner had ever run); and Scorecard's own words: "score is 0: SAST tool is not run on all commits … 0 commits out of 30 are checked with a SAST tool".

## Part 2 — The workflow

`.github/workflows/codeql.yml`. Every choice in it, and why:

| Choice | What | Why |
|---|---|---|
| When | every pull request (no path filter), `main` after each merge, Mondays 04:41 UTC, by hand | A scan only protects what it sees. No filter on pull requests so that it can become a required check later without a docs-only pull request waiting for a result that never comes |
| Token | `permissions: {}` at the top; the job gets `contents: read` and `security-events: write` | Read the code, write the result to the Security tab, nothing else |
| What it scans | `python` (services, scripts, migrations, evals) and `actions` (the workflow files: CI is code and holds a token) | These are the two languages in the repository. Nothing is built (`build-mode: none`), so there is no image and no compile |
| Where results go | `category: /language:<language>` | Two uploads under one category and the second replaces the first |
| Pins | every action is a full commit SHA with the tag in a comment; `init` and `analyze` are on the same release | A tag can be moved, a SHA cannot (the repository's rule since ADR-0010). To find a SHA for an annotated tag, ask for the tag, and if its type is `tag`, ask once more for the commit it points to (`gh api repos/github/codeql-action/git/tags/<sha>`) |
| Checkout | `persist-credentials: false` | The scan does not need to push anything, so it keeps no credentials |
| A busy day | a newer push cancels the scan of a pull request; a scan of `main` is never cancelled | The scan of `main` is the one that opens and closes alerts |
| Time limit | 30 minutes | A stuck job is stopped instead of running for the default six hours |

## Part 3 — The tests, written first

```bash
python -m pytest scripts/dev/test_codeql_workflow.py scripts/dev/test_repo_hygiene.py -q
```

Before the workflow existed all 9 new tests failed (the file was not there). With it: 22 pass (the 9 new and the 13 that were already there; the existing pin test now covers the new file too). To see them guard something, break the workflow and run them again. Thirteen ways were tried and every one was caught by the test meant to catch it:

| Broken on purpose | Caught by |
|---|---|
| a path filter on pull requests; a push trigger on every branch | the triggers test |
| a daily schedule | the weekly-schedule test |
| a broad token at the top; a job that can write the code | the permissions test |
| no scan of the workflow files; a build step for Python | the languages test |
| fail-fast on | the one-language-failing test |
| `init` and `analyze` on different releases | the same-release test |
| one category for both languages | the category test |
| a checkout that keeps credentials | the checkout test |
| a six-hour time limit | the time-limit test |
| a moving tag (`@v7`) instead of a commit | the existing pin test in `test_repo_hygiene.py` |

## Part 4 — The first scan: the pull request's own run

Open the pull request. CodeQL starts next to the other checks.

```bash
gh pr checks <N>
gh api "repos/OWNER/REPO/code-scanning/analyses?ref=refs/pull/<N>/merge" --jq '.[] | {category, results_count, rules_count}'
```

Result on 2026-11-07 (pull request 333): both jobs passed, **0 alerts**. `codeql python (static analysis)` took 1 min 2 s and `codeql actions (static analysis)` 38 s; the whole set of checks finished in about two and a half minutes, so a pull request waits no longer than before. GitHub's own check, named `CodeQL`, passed in 2 s.

**Read that 0 with care.** On a pull request CodeQL reports only the alerts in the lines the pull request adds or changes (GitHub calls this diff-informed analysis, and the action does it by itself; see [Incremental analysis](https://docs.github.com/en/code-security/how-tos/find-and-fix-code-vulnerabilities/scan-from-the-command-line/incremental-analysis)). So a clean pull request says nothing about the code it did not touch, and pull request 333 changed a single Python file. The first version of this lab, and of the pull request description, read that 0 as "the repository is clean". It is not: Part 4b shows what the whole repository held.

The scan did read everything, which is worth checking. In the Python job's log (`gh run view <run> --job <job> --log`): the extractor read **268 files** (count the lines that say "Extracted file") and **45 queries** were loaded (SQL injection, unsafe file paths, unsafe deserialisation, the Flask debugger and so on). In the workflow job: **18 queries** over the 8 workflow files.

## Part 4b — The first full scan: `main`, after the merge

Once the pull request is merged, the `push` trigger scans `main` and reports **everything**, old code included. This is the real first reading.

```bash
gh api "repos/OWNER/REPO/code-scanning/analyses?ref=refs/heads/main&tool_name=CodeQL" --jq '.[] | {category, results_count, rules_count}'
gh api "repos/OWNER/REPO/code-scanning/alerts?state=open&tool_name=CodeQL" \
  --jq '.[] | {number, rule: .rule.id, severity: (.rule.security_severity_level // .rule.severity), path: .most_recent_instance.location.path, line: .most_recent_instance.location.start_line}'
```

Result on 2026-11-07 (the scan of `main` at the merge commit, 1 min 27 s): the workflow files, **0 alerts**; the Python, **1 alert**. It is `py/bind-socket-all-network-interfaces` (medium) in `services/shared/tests/test_metrics.py` line 86: "Binding a socket to all interfaces (using '0.0.0.0') is a security risk." The pull request's scan had not shown it because that pull request did not touch the file.

**Triage.** The line is in a test that takes a port on purpose, on the same all-interfaces address the metrics server binds, so that the server's "port already taken" path is exercised; it listens for one test on a random port. It is a false alarm of the `used in tests` kind. A fix would mean editing `services/`, and any change there starts `release.yml` (a rebuild of every image and a new release pull request), which is not worth doing for a test. It was dismissed with a written reason (Part 6).

## Part 5 — Prove it can find something

A scan that finds nothing proves little until it has been seen finding something. A throwaway pull request (#334, a draft, never merged, closed afterwards) added two files that make mistakes on purpose.

| Planted on purpose | CodeQL rule | Severity |
|---|---|---|
| a database query built from a web request | `py/sql-injection` | high |
| a shell command built from a web request | `py/command-line-injection` | critical |
| the Flask debugger left on | `py/flask-debug` | high |
| a workflow that pastes an issue title into a shell command | `actions/code-injection/critical` | critical |

All four were caught. GitHub's `CodeQL` check went **red**: "4 new alerts including 2 critical severity security vulnerabilities". That is what a required check would do to a pull request that adds a serious mistake.

```bash
gh api "repos/OWNER/REPO/code-scanning/alerts?ref=refs/pull/<N>/merge&tool_name=CodeQL" \
  --jq '.[] | {number, rule: .rule.id, severity: (.rule.security_severity_level // .rule.severity), path: .most_recent_instance.location.path, line: .most_recent_instance.location.start_line}'
```

**The surprise.** The first version of the planted workflow was **not** flagged. It had `permissions: {}`, an empty token, and CodeQL calls an injection critical only where the job has something worth stealing. Given `issues: write` (as a triage bot would have) it was caught. Two lessons: Kaval's own workflows start with an empty token, which is part of why the real scan is quiet; and a scan is only proven when its quiet and its noise have both been seen.

## Part 6 — Reading and dismissing an alert

Security tab, Code scanning, filter on the tool `CodeQL`. An alert gives the rule and its severity, the file and line, and, for injection, the path the bad input takes from where it enters (the source) to where it does harm (the sink). Decide each one:

- **Fix it** when the fix is small and safe.
- **Dismiss it** when it is not a real problem or not worth fixing, and say why in one sentence. The reasons GitHub allows are `false positive`, `won't fix` and `used in tests`. A dismissal stays in the Security tab with its reason and can be reopened (`-f state=open`). Never dismiss without a comment.

```bash
gh api -X PATCH repos/OWNER/REPO/code-scanning/alerts/<N> \
  -f state=dismissed -f "dismissed_reason=used in tests" -f "dismissed_comment=<one sentence of why>"
```

The first full scan raised one alert (number 14, Part 4b). It was dismissed as `used in tests` with the comment "Test only: it takes a port on purpose, on the same all-interfaces address the metrics server binds, so that the 'port already taken' path is exercised. It listens for one test on a random port." The four planted alerts (numbers 10 to 13) were dismissed the same way with the comment "KAV-110 detection control (pull request 334): unsafe on purpose, never merged", which also proved the command.

## Part 7 — Scorecard, after the merge

Scorecard gives a CodeQL workflow 7 of the 10 points for being there, and the other 3 for the share of the last 30 merged pull requests that ran a scanner (about one tenth of a point per pull request). So after the merge the SAST check should read about **7** and climb toward 10. Run it by hand and read it:

```bash
gh workflow run scorecard.yml
gh api "repos/OWNER/REPO/code-scanning/alerts?state=open&per_page=100" --jq '.[] | select(.rule.id == "SASTID") | .most_recent_instance.message.text'
```

Recorded on 2026-11-07, run by hand after the merge (it took 30 seconds): **SAST 7** (it was 0), with the message "SAST tool detected but not run on all commits: 3 commits out of 30 are checked with a SAST tool", as predicted. The alert (number 6) stays open until the score reaches 10.

## Part 8 — Making it a required check, later

Not today. [ADR-0080](../adr/0080-codeql-scans-every-pull-request-and-starts-as-advice.md) says when: seven days in a row in which every scan finished without CodeQL's own failure, and every open alert has a decision. Then the two checks `codeql python (static analysis)` and `codeql actions (static analysis)` are added to the branch rule for `main` (Settings, Branches), a GitHub setting that is shown first and done only on Roshan's own go. Whether to also require the `CodeQL` check (the one that went red in Part 5) is decided then.

## Part 9 — Turning it off, and what can go wrong

- **Off:** `gh workflow disable codeql.yml`, or delete the file. Existing alerts stay in the Security tab.
- **A red `CodeQL` check on a pull request** is advice for now. Read its annotations, then fix or dismiss.
- **Dependabot's pull requests** are scanned too (GitHub allows the upload on the `pull_request` event). A merge commit *authored by Dependabot* (merging with Dependabot's own "squash and merge" command) runs with a read-only token and fails the upload; merging through GitHub's button as yourself avoids it.
- **A new language** (the mobile app's TypeScript, later): add a row to the matrix and to the test. The test names the exact set so the change is deliberate.
- **A job that takes more than 30 minutes** is stopped. Look at the log before raising the limit.

## Part 10 — Results

| What | Result |
|---|---|
| Tests | 9 written first and seen failing; with the workflow 22 pass (9 new, 13 existing); 13 deliberate breakages all caught |
| Scan on the pull request | 0 alerts, but only the changed lines are reported; it read 268 Python files with 45 queries and the 8 workflow files with 18 |
| First full scan (`main`) | 1 alert (medium, in a test that opens a port on purpose), dismissed as used in tests; the workflow files 0 |
| Scorecard SAST | 7 (it was 0): "3 commits out of 30 are checked with a SAST tool" |
| Time | Python job 1 min 2 s, workflow job 38 s, alongside the other checks (whole set about 2.5 minutes) |
| Detection control | 4 of 4 planted mistakes caught (2 critical, 2 high); `CodeQL` check red; first workflow version missed (empty token), second caught |
| Cost | $0 |

## Done when

- [x] `codeql.yml` exists and its tests pass
- [x] The scan ran on the pull request (nothing in the changed lines) and on `main` after the merge (one alert, a test-only false alarm, dismissed with a reason)
- [x] A throwaway pull request proved it finds planted mistakes; the four alerts were dismissed with a reason and the branch deleted
- [x] The decision on a required check is recorded (advice first, a gate after a clean week: ADR-0080)
- [x] Merged (pull request #333) and the first scan of `main` has run
- [ ] The first scheduled Monday scan (2026-11-09, 04:41 UTC) has been seen
- [x] Scorecard's SAST reading after the first run on `main` is recorded: 7

## Clean up

The throwaway pull request is closed and its branch deleted on GitHub and on the laptop. Nothing else was created.
