# Ownership: who answers for what, and what happens when something breaks

Kaval is run by one person. This page is written anyway, because "one person" is an answer a
reader has to be able to find, and because the gaps it leaves should be stated, not discovered.

Written 2026-11-15 (`KAV-155`).

## Who owns what

| Thing | Owner | Stand-in |
|---|---|---|
| Production and staging (servers, database, cluster) | Roshan | None |
| Approving or denying what Kaval proposes | Roshan, in Slack | None: an unanswered request expires, it is never approved by default |
| Releases and promotions to production | Roshan (merges the promotion pull request) | None |
| The AWS account and its bill | Roshan | None |
| Secrets and their rotation | Roshan ([schedule](architecture/secret-rotation.md)) | None |
| Security reports from outside | Roshan, through GitHub's private reporting ([SECURITY.md](../.github/SECURITY.md)) | None |
| Code review | Roshan; `CODEOWNERS` names him for every path | None: on a one-person repository a review cannot be required |

**There is no on-call rota and no promise to answer at night.** Production is parked most of the
time and stops itself at 02:00 IST. The service levels Kaval does promise are in
[service-levels.md](architecture/service-levels.md).

## How a problem reaches the owner

| What happened | How it arrives | First thing to read |
|---|---|---|
| Kaval found an incident it can propose a fix for | A card in Slack with Approve and Deny | The card; then the runbook it names |
| Kaval found something only a person should handle (full disk, slowness, waste) | A message in Slack, no buttons | [docs/runbooks/](runbooks/README.md) |
| Nobody answered an approval | A reminder at 15 minutes, a louder message at 30, expiry at 4 hours | [approval-unanswered](runbooks/approval-unanswered.md) |
| Kaval itself is unhealthy | A Slack message starting "Kaval's own health" | The runbook for that alert |
| Kaval is so broken it cannot say so | An email: the still-alive alarm in AWS, outside the cluster | [still-alive-alarm](runbooks/still-alive-alarm.md) |
| Spending is running high | Emails at $42 and $46; a hard stop at $48 | [cost-spike](runbooks/cost-spike.md) |
| The nightly database dump is missing | A failed daily check on GitHub, which emails the owner | [backup-missing](runbooks/backup-missing.md) |

## Declaring an incident

An incident is declared when production is doing harm or cannot do its job, and a person has to
act. Kaval opening an incident row for a crashed demo pod is its normal work and is not this.

1. **Say so in writing first:** one line in the Slack channel with the time and what is seen.
   The line is the start of the timeline.
2. **Stop the harm before finding the cause.** In order of preference: Deny the open request;
   roll back with `rollback.yml` ([runbook](runbooks/rollback-prod.md)); park production with
   `make down`, which takes a database dump first. Parking is always allowed and always safe.
3. **Fix, or leave it parked** until there is time to fix it properly.
4. **Write a post-mortem** in `docs/postmortems/` within a week: timeline, cause, what stopped it
   being worse, what changes. No blame; the format of the existing ones.

## Escalation

| Step | When | To |
|---|---|---|
| 1 | Always | The owner, in Slack |
| 2 | An approval unanswered for 30 minutes | The louder Slack message, to the same owner |
| 3 | An approval unanswered for 4 hours | Nobody: the request expires and nothing is executed |
| 4 | A fault only a person can fix, open for 2 hours | A second Slack message |
| 5 | AWS itself is at fault, or the account is at risk | AWS Support (the account's basic plan: billing and account questions) |

There is no step where a second person is called, because there is no second person.

## Recurring duties

| When | What | Where it is written |
|---|---|---|
| Every Monday | Read the weekly Scorecard and CodeQL results | [Lab 71](labs/lab-71-a-security-policy-owners-and-a-weekly-scorecard.md), [Lab 81](labs/lab-81-turning-on-codeql-and-reading-what-it-says.md) |
| As they arrive | Read and merge, or close, each Dependabot pull request | [contributing.md](contributing.md) |
| Monthly (an e-mailed issue on the 1st) | Work the check-in: scheduled runs alive, the image watch, Dependabot, the Scorecard, CodeQL, the bill, a restore drill, dated reminders | [monthly-check-in](runbooks/monthly-check-in.md) |
| Monthly | A restore drill: `make restore-drill` | [restore-drill](runbooks/restore-drill.md) |
| Monthly | The bill by service and environment: `make cost-showback` | `docs/cost/` |
| Every 90 days (last 2026-11-16, next by 2027-02-14) | Change the AWS access key on the laptop | [secret-rotation.md](architecture/secret-rotation.md) |
| Each November | Change every other secret | [secret-rotation.md](architecture/secret-rotation.md) |
| When a dated exception nears its end | Remove it or renew it with a reason | The ADR that granted it |

## Said plainly

- **One person is a single point of failure.** If the owner is away, nothing is approved and
  nothing breaks further: requests expire, production parks itself each night, and the budget's
  hard stop holds. Kaval is built to fail towards doing nothing.
- **Nobody is paged.** A problem at 03:00 IST is read in the morning. For a learning project
  whose production is mostly parked that is the honest service level.
- **What a second maintainer would change:** a required review on every pull request, a second
  approver on promotions, and a real escalation step 2.
