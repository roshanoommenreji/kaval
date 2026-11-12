# Lab 93 — Hiding the account number in public logs

**Phase:** 8 (hardening) · **Story:** `KAV-141` · **Decision:** [ADR-0091](../adr/0091-the-account-number-is-hidden-in-public-logs-and-role-addresses-are-secrets.md) · **Cost:** $0 · **Time:** 30 minutes, plus one release round for Part 4

## What you will build

A public repository has public build logs. After this lab, no new log line of a Kaval workflow shows the AWS account number: the role address is a *secret* (shown as `***` from the first line) and the login step
hides the number from everything after it.

## Part 1 — See the leak first

Open any release run from before this change and search the log for `.dkr.ecr.`: the registry address, so the account number, is there in plain text. The step header of the login step also shows the role address,
because it came from a repository *variable*. Do not paste what you find anywhere public.

## Part 2 — Read the change

In each of `release.yml`, `promote.yml`, `rollback.yml`, `backup-watch.yml`, the `configure-aws-credentials` step now has:

```yaml
role-to-assume: ${{ secrets.AWS_PUBLISH_ROLE_ARN }}   # or AWS_BACKUP_WATCH_ROLE_ARN
mask-aws-account-id: true
```

## Part 3 — The tests

```bash
python -m pytest scripts/dev/test_workflow_account_number.py -q
```

They pin: masking on in all four login steps; the role from a plain secret reference; no variable named like a role, ARN or account; no 12-digit number in any workflow. Three of them run the check on made-up
workflows that break a rule (masking left off, masking `false`, role from a variable, role as a literal) and expect it to complain. To see them bite, delete `mask-aws-account-id: true` from one workflow and run
the tests: that workflow's test fails.

## Part 4 — Set the secrets, then the live proof (the owner's go; order matters)

1. **Set the secrets first** (a secret nobody has set makes the login step fail). The values are the existing variables' values, read from GitHub or from Terraform's outputs and piped in so they never appear on
   screen or in git:
   ```bash
   gh variable get AWS_PUBLISH_ROLE_ARN       | gh secret set AWS_PUBLISH_ROLE_ARN
   gh variable get AWS_BACKUP_WATCH_ROLE_ARN  | gh secret set AWS_BACKUP_WATCH_ROLE_ARN
   gh secret list                             # names only
   ```
2. Merge the pull request. The old workflows ignored the new secrets, so setting them first breaks nothing.
3. Run **release** from the Actions tab (a new commit on `main` is needed for it to build; the staging-pin pull request it opens for a docs-only commit is closed unmerged).
4. Search the finished run's whole log for the number (it is in `~/.aws` or your notes; do not write it in a file):
   ```bash
   gh run view <run-id> --log | grep -c "<account number>"      # expect 0
   gh run view <run-id> --log | grep -c "dkr.ecr"               # lines exist, with *** in place of the number
   ```
5. Run **backup-watch** once (`gh workflow run backup-watch.yml`) and **promote** with an already-promoted tag if you want the other two covered; `promote` and `rollback` refuse on their own checks, which is also
   a pass for this lab as long as the login step succeeded and the log search is 0.
6. Only after a clean proof, delete the two old repository variables (`gh variable delete AWS_PUBLISH_ROLE_ARN`, `gh variable delete AWS_BACKUP_WATCH_ROLE_ARN`).

Result: _to be filled in after the live proof._

## Part 5 — The old logs (a decision, not a step)

Earlier runs still show the number. `gh run list --limit 200 --json databaseId,workflowName` lists them; `gh run delete <id>` removes one for good (irreversible, and it also removes the run's artifacts such as
the parts lists). The records bound to images (build record, parts list) live in GitHub's attestation store, not in run logs, and are not affected. Do this only if you decide to.

## What to take away

An address is not a credential, but "public log" means anyone, forever. Two small settings (secret instead of variable, masking on) close it, and a test keeps them closed. The same lesson as before: measure what
the log really shows instead of assuming it hides things.
