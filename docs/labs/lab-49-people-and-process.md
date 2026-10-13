# Lab 49 — People and process: approvals nobody answers, backups that are watched, a database server that is monitored

**Phase:** 5, slice 6 · **Cost:** about $0 a month (nothing recurring); the staging proof is about 30 minutes of
staging (~$0.03)

Slices 1 to 5 proved Kaval *acts* correctly. This slice is about the people around it: what happens
when nobody answers, when a backup silently stops, when the database server itself fills up. Three
parts, one pull request each. This lab grows as they land; the parts built so far are below.

Decisions: [ADR-0048](../adr/0048-approvals-nobody-answers-expiry-and-escalation.md). Runbook:
[`approval-unanswered.md`](../runbooks/approval-unanswered.md).

## Prerequisites

- [Lab 48](lab-48-chaos-disk-full-and-a-slow-service.md) done; Docker running (a throwaway Postgres for the tests)
- `AWS_PROFILE=kaval` for the staging proof

---

## Part A — approvals nobody answers

### Step 1 — the rule, tested against a real database

```bash
pytest services/gateway/tests/test_approvals.py services/shared/tests/test_db_roles.py \
       services/executor/tests/test_executor_selection.py scripts/dev/test_chart_approvals.py -q
```

Read the negative tests first: nothing is sent before 15 minutes; a decided request, a `never` request and one
nobody was told about get no reminder; a click one second before the expiry is recorded and one at the expiry is
refused for **both** approve and deny; unusable settings (`0`, `nan`, `inf`, a reminder after the escalation)
fall back to 15 / 30 / 240; a Slack outage is retried rather than remembered.

### Step 2 — the permission bug the design found

```bash
pytest services/shared/tests/test_db_roles.py -q       # the gateway may mark a Slack message on an action, and nothing else
```

Remove the line `GRANT UPDATE (slack_notified_at, slack_ts) ON action TO kaval_gateway;` from
`scripts/ops/db-roles.sql` and run it again: it fails. Put the line back.

### Step 3 — staging: leave a request unanswered and watch it

```bash
AWS_PROFILE=kaval make staging-up
AWS_PROFILE=kaval make approvals-file        # files one harmless request; prints ACTION <id>
AWS_PROFILE=kaval make approvals-report      # now, ~4 min in, ~8 min in, and after 15 minutes
AWS_PROFILE=kaval make chaos-approve ACTION=<the code it printed>   # after 15 minutes: expect HTTP 409 "expired"
```

Staging has no Slack, so `approvals-report` runs the real notifier as a dry run inside a transaction it rolls back:
it prints the exact reminder, escalation and expiry messages and sends and stores nothing. The live part is the
deployed gateway refusing the late approval.

## Part B — backups that are watched, restores that are drilled

Decisions: [ADR-0049](../adr/0049-backups-watched-and-restores-drilled.md). Runbooks:
[`backup-missing.md`](../runbooks/backup-missing.md), [`restore-drill.md`](../runbooks/restore-drill.md).
Numbers: [disaster-recovery.md](../architecture/disaster-recovery.md).

### Step 4 — the rules, tested without AWS

```bash
pytest scripts/ops/test_backup_watch.py scripts/ops/test_restore_drill_record.py -q
```

Read the negative tests first: a **parked** production owes no dump and raises nothing; a server that booted
five minutes before 19:30, or stopped before 19:40, owes nothing; a dump from the wrong hour does not count; a
launch that failed never ran; a 35-day-old drill is fine and a 36-day-old one is overdue; an empty database passes
the drill with a visible note, and a dump from before a later migration is not failed for lacking that
migration's table (the first version of the rule did, and the first real drill found it).

### Step 5 — drill a restore for real

```bash
AWS_PROFILE=kaval make restore-drill      # Docker running; reads production's newest dump, writes docs/drills/restore-<date>.md
```

On 2026-10-13: the newest dump was 63 hours old (no dump on the nights production was parked), 24 KB, restored
strictly in 1 second, and production held no signals or incidents, so the record passes **with two notes**
saying so. It estimates recovery at about 5 minutes against a target of 30.

### Step 6 — the role and the daily check (needs your say-so, because it applies to production)

```bash
cd infra/envs/prod && terraform plan       # read it: one role and one policy to add, nothing else
terraform apply                            # only when asked; IAM is free
gh variable set AWS_BACKUP_WATCH_ROLE_ARN --body "$(terraform output -raw backup_watch_role_arn)"
gh variable set PROD_BACKUP_BUCKET --body "$(terraform output -raw backup_bucket_name)"
gh workflow run backup-watch               # then read the run
```

Expect `OK` while production is parked. To see it raise, run the same script with a history in which production
was up and no dump followed (the unit tests do exactly this).

## What went wrong

- *(filled in with the staging proof)*

## Done when

- [x] A reminder, a louder message and an expiry exist, with the refusal enforced where the decision is written (tests)
- [x] The executor refuses a stale approval (tests)
- [x] A fault that is only for a person escalates after two hours (tests)
- [ ] The staging proof: a request left to expire, refused with 409 by the deployed gateway
- [x] Part B built: the rules (tests), the read-only role (validated), `make restore-drill` run for real and recorded
- [ ] Part B live: the role applied to production, the two repository variables set, the daily check's first run
- [ ] Part C: database server monitoring
