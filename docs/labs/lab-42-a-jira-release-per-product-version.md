# Lab 42 — A Jira Release for every product version

**Phase:** 4 · **Story:** `KAV-35` (follow-up) · **Cost:** $0 (no AWS; it only talks to Jira)
Decision: [ADR-0013](../adr/0013-component-versions-and-release-naming.md), point 6, and its 2026-10-10 amendment.

Kaval's change record says which product version shipped, which component versions were inside it and which Jira
issues its commits named ([Lab 36](lab-36-versions-tags-and-change-records.md)). Jira did not know any of that.
This lab makes Jira's own **Release** (also called a *fix version*) for each product version, so the Jira board can
answer "what shipped in Kaval 0.1.0?" without anyone reading commits.

## What you need

- The repo's `.env` with `JIRA_SITE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` (the same ones every `jira-sync.py` verb uses).
- A change record for the version: `docs/releases/<date>-vX.Y.Z.md`. `promote.yml` writes it in the promotion pull request.

## The steps

1. **Look first.** From the repo root:

   ```
   make jira-release VERSION=0.1.0
   ```

   It prints the Release name, its date and the issues it would file, and says `dry run: nothing written`.
   Nothing in Jira changes.

2. **Do it.**

   ```
   make jira-release VERSION=0.1.0 APPLY=1
   ```

   It creates the Release `Kaval 0.1.0` (description = the components line, release date = the record's date, marked
   released), then sets **Fix versions** on each issue the record names. Each issue prints `added`.

3. **Run it again.** Every issue prints `skip (already in Kaval 0.1.0)` and nothing is written. That is the proof it
   is safe to repeat, for example after a record is corrected.

4. **See it in Jira.** Project **KAV** → **Releases** lists `Kaval 0.1.0`; open it to see its issues. Or search
   `fixVersion = "Kaval 0.1.0"`.

## How it works

- `scripts/tracking/jira_release.py` reads the record: the `# Release vX.Y.Z — date` heading, the `Kaval X.Y.Z: ...`
  components line, and the keys under `## Issues`. It touches no network, so it is unit-tested
  (`test_jira_release.py`), including a test that the real `v0.1.0` record still parses.
- `jira-sync.py release` asks Jira for the project's existing Releases (`GET /rest/api/3/project/KAV/versions`),
  creates the missing one (`POST /rest/api/3/version`), and adds it to each issue's Fix versions with an `add`
  update, which never removes a Release an issue already has.

## What it proved (2026-10-10, live)

`Kaval 0.1.0` was created released on 2026-10-09 with the nine issues the baseline record names (KAV-32, 51, 55, 56,
57, 58, 59, 60, 61). A second run changed nothing. A Jira search on the fix version returned the same nine.

## Limits

- **Manual on purpose.** The workflow that writes the record has no Jira access (ADR-0035), so you run this after the
  promotion pull request merges.
- **Only issues named in commits.** A change whose commit message has no `KAV-n` is not in the record, so it is not in the
  Release. The commit-message check (`check_commits.py`) is what keeps that rare.
- **Marked released at once.** A record exists only for a version promoted to prod. If you ever run it before the merge,
  the Release will say released early; fix it by hand in Jira.
- **The baseline lists issues, not stories closed in `0.1.0`.** `0.1.0` is the first version, so its record covers the
  39 commits since the previous prod image, not the whole project's history.

## If it goes wrong

- `no change record for v0.2.0 in docs/releases` — the promotion pull request has not been merged, or the version is typed wrong.
- `HTTP 401` — the token in `.env` has expired; make a new one (Atlassian account → Security → API tokens).
- `skip (not readable in Jira: HTTP 404)` for a key — the key exists in a commit message but not in Jira (a typo); the
  rest still go in.
