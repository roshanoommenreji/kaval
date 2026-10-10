# Lab 34 — `promote.yml`: the gate between staging and prod

**Phase:** 4 · **Story:** `KAV-63` · **Cost:** nothing (GitHub Actions on a public repository, a few
read-only AWS and Jira calls). Prod stays parked; nothing is applied.

[Lab 33](lab-33-staging-smoke-and-passed-record.md) ended with a file saying which image digests passed
staging. This lab builds the thing that reads it: a workflow that refuses anything not on that file, and a
CI check that stops anyone going round it. The decisions are in
[ADR-0032](../adr/0032-promote-workflow-and-the-promotion-guard.md).

**Result:** `promote.yml` run with `sha-2459417` passes four checks and opens a pull request that points
prod at it; run with `sha-ffb436b` it refuses. A hand edit of prod's tags to an untested tag fails a
required CI check.

---

## 1. What exists

| Piece | Where | Job |
|---|---|---|
| The brain | `scripts/release/promote.py` (+ `test_promote.py`, 28 tests) | `verify`, `pin`, `body`, `guard` |
| The workflow | `.github/workflows/promote.yml` | `gate` (read-only) then `propose` (opens the pull request) |
| The backstop | `promotion-guard` job in `.github/workflows/ci.yml` | fails a pull request that moves prod to an unrecorded tag; a required check |

## 2. One-time setup: three Jira secrets

The gate reads Jira to see whether `uat` stories are signed off. You create these yourself; the values never
go into git or into a chat.

1. At `id.atlassian.com` → Security → API tokens → **Create API token with scopes**, app Jira, a read scope
   (`read:jira-work`). Copy the token.
2. Find your cloud id: open `https://<your-site>.atlassian.net/_edge/tenant_info`.
3. In the repository folder:

   ```bash
   gh secret set JIRA_API_TOKEN     # paste the token at the hidden prompt
   gh secret set JIRA_EMAIL         # the Atlassian account email
   gh secret set JIRA_SITE_URL      # https://api.atlassian.com/ex/jira/<cloud id>
   gh secret list                   # names only, never values
   ```

A scoped token only works through that gateway address, not through the normal site address.

## 3. Run it

```bash
gh workflow run promote.yml --ref main -f tag=sha-2459417
```

The `gate` job prints:

```
release sha-ffb436b -> sha-2459417: stories KAV-32, KAV-51, ...
  PASS  tag is on the passed-staging record: sha-2459417 passed 2026-10-08T17:27:51Z
  PASS  tag is newer than what prod runs: prod runs sha-ffb436b; sha-2459417 is ahead of it
  PASS  ECR digests equal the digests that passed: all four match
  PASS  every uat story in the release is signed off: 9 stories, 0 with uat, all accepted
sha-2459417 may be promoted to prod.
```

then `propose` opens `chore(release): promote sha-2459417 to prod` with the evidence in its description.
CI's first run on that bot pull request waits for a person: Actions tab → **Approve and run**. Merging is the
go/no-go. **Merging changes what prod runs the next time it is brought up, including a database migration.**

## 4. Run it wrong

```bash
gh workflow run promote.yml --ref main -f tag=sha-ffb436b     # prod's current tag; never on the record
```

`FAIL  tag is on the passed-staging record: sha-ffb436b never passed staging`, and `propose` is skipped.

The `uat` check, tried against real Jira: `KAV-44` is labelled `uat` and still *In Staging*, so a release
containing it is refused; `KAV-40` (`uat`, *Done*) is accepted.

## 5. The backstop

```bash
python scripts/release/promote.py guard --base origin/main
```

- Prod's tags unchanged: passes.
- Hand-edit the tags to `sha-deadbee` (not on the record): `FAIL ... not on the record on the base branch`.
- Run `promote.py pin --tag sha-2459417`: passes, because that tag is on the record.

The record is read from the **base** branch, so one pull request cannot add a record entry and use it in
the same change.

## 6. What went wrong, in order

1. **Wrong page for the token.** The Atlassian *admin* "API keys" page offers only `:admin` scopes (accounts,
   domains, groups) and no Jira scopes. The right place is the personal token page under `id.atlassian.com`.
2. **Wrong address for a scoped token.** `JIRA_SITE_URL` first held the normal site address copied from
   `.env`, which a scoped token is not accepted on. Replaced with the gateway address before the first run;
   the first run then passed.
3. **My own newline slip.** The first draft of the pull-request text had real line breaks where `\n` was
   meant (a script edit that did not do what it said). Caught by a test and rewritten with named constants.
4. **An unrelated red CI.** Two new CVEs in the bundled OPA binary made the image checks fail on every pull
   request. v1.21.1 was no help (same Go version). Handled by a dated, path-scoped exception
   ([ADR-0033](../adr/0033-dated-trivy-exception-for-the-bundled-opa-binary.md), `KAV-64`).

## What this does not do

- It does not roll back. An older tag is refused as "a rollback"; that is [`rollback.yml`](lab-35-rollback-workflow.md)
  (`KAV-66`), and the guard now also accepts a tag prod has pinned before.
- It does not check the backup image, which prod does not run yet.
- Which stories are in a release comes from commit subjects. A commit without a `KAV-n` in its subject is
  invisible to the `uat` check.
- It does not stop an administrator bypassing branch protection, and the record is not cryptographically signed.

## Reproduce

1. Set the three secrets (section 2).
2. `gh workflow run promote.yml --ref main -f tag=<a tag in passed-staging.json>`; confirm the four PASS lines
   and the new pull request. Close it unmerged if you are only practising.
3. Run it again with a tag that is not in the record; confirm the refusal.
4. `python scripts/release/promote.py guard --base origin/main` after hand-editing a prod tag; confirm FAIL.
