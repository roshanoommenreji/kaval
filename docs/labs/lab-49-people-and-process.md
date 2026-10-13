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

## What went wrong

- *(filled in with the staging proof)*

## Done when

- [x] A reminder, a louder message and an expiry exist, with the refusal enforced where the decision is written (tests)
- [x] The executor refuses a stale approval (tests)
- [x] A fault that is only for a person escalates after two hours (tests)
- [ ] The staging proof: a request left to expire, refused with 409 by the deployed gateway
- [ ] Part B: the missing-dump alert and the restore drill
- [ ] Part C: database server monitoring
