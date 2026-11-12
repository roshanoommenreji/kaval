# ADR-0091: the account number is hidden in public logs, and the role addresses are secrets

**Status:** Accepted on 2026-11-13 (Roshan: "go 141"). Built, tested and proven live on 2026-11-13 (secrets set on his go; release run 43304245593: account number found 0 times in the log). Open: his decision on deleting older run logs.
**Date:** 2026-11-13
**Related:** [ADR-0030](0030-ci-publishes-images-by-oidc-and-staging-is-pinned-by-pull-request.md) (put the role address in a repository *variable*; amended by this ADR),
[ADR-0049](0049-backups-watched-and-restores-drilled.md) (same for the backup check), [ADR-0090](0090-images-are-signed-keyless-and-the-signature-lives-in-the-registry.md) (the live proof that found the leak),
[threat model](../architecture/threat-model.md), [Lab 93](../labs/lab-93-hiding-the-account-number-in-public-logs.md), `KAV-141` (this story)

## Context

CLAUDE.md rule 5: no real account numbers in committed files, and the repository is public. The live proof of `KAV-138` showed the same number printed in **plain text in the public GitHub Actions logs** of
every release run. Two causes, both measured:

1. The role address (`arn:aws:iam::<account>:role/...`) was stored as a repository **variable**. GitHub prints a step's inputs in the step header, and hides only *secrets* from the first line.
2. `aws-actions/configure-aws-credentials` hides the account number from later log lines only when told to (`mask-aws-account-id`, **off by default**). So the registry address (`<account>.dkr.ecr...`) in the
   login, push and signing steps was printed too.

An account number is not a credential: the role's trust policy decides who can use it. But it helps an attacker target us, and it breaks our own rule.

## Options (what real companies do)

1. **Do nothing; accept it** (many open-source projects do). Rejected: it breaks our rule 5 and costs nothing to fix.
2. **Turn on the action's masking only.** Hides the number from later lines, but the step header, printed before the action runs, still shows the role address from a variable.
3. **Masking on, and the role address as a secret.** Secrets are hidden from the first line; the masking covers every later line. This is what the action's own documentation and most companies' pipelines do.
4. **Keep the numbers out of GitHub altogether** (look the role up by name at run time). Not possible: assuming a role needs its full address.

## Decision

**Option 3**, in all four workflows that log in to AWS (`release.yml`, `promote.yml`, `rollback.yml`, `backup-watch.yml`).

- `mask-aws-account-id: true` on every login step.
- `role-to-assume` reads `secrets.AWS_PUBLISH_ROLE_ARN` (release, promote, rollback) and `secrets.AWS_BACKUP_WATCH_ROLE_ARN` (backup check). The values are the same addresses as before; only where they are kept changes.
- Left as a variable: `PROD_BACKUP_BUCKET`. A bucket name is not the account number and nothing in it identifies the account.
- **A test keeps this true** (`scripts/dev/test_workflow_account_number.py`): every login step turns masking on; the role comes from a plain `secrets.` reference (not a variable, not a literal); no workflow reads a
  role/ARN/account variable; no workflow holds a 12-digit number. It also runs on made-up workflows that break each rule, so the check is shown to refuse.
- **Order matters:** the secrets are set on GitHub **before** the pull request merges. A workflow that reads a secret nobody has set fails at the login step. The old variables are deleted only after the live proof.

## Consequences

- **Cost:** $0.
- **Security:** the number disappears from new logs. It does **not** disappear from old logs: every earlier run of these workflows still shows it until the run is deleted. Deleting old runs (`gh run delete`) is a
  separate, irreversible decision for the owner; it is not done here unasked, and the proof lists how many runs are affected.
- **Failure and off switch:** if a secret is missing or wrong, the login step fails and nothing is published, promoted or rolled back (fail closed). Rollback of the change is one revert of the pull request plus
  re-adding the variables.
- **Observability:** the live proof searches a fresh release run's whole log for the number and expects zero hits; Lab 93 gives the same command for any later run.
- **Honest limit:** masking is exact-text matching. A value that is altered before printing (for example split or encoded) would not be hidden. Our logs print it as the registry host and role address only, which the proof checks.
- **Not covered:** the number is also visible to anyone with access to the AWS account or to repository settings; that is intended.
