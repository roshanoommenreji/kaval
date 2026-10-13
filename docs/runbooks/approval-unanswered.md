# A fix is waiting for an answer, or its request has expired

Kaval proposed a fix that needs a person ([ADR-0048](../adr/0048-approvals-nobody-answers-expiry-and-escalation.md)).
It reminds after 15 minutes, escalates after 30 and **expires after 4 hours** (staging: 3, 7 and
15 minutes). An expired request cannot be approved or denied; nothing was done.

## Signals

- Slack: ":hourglass_flowing_sand: Still waiting for an answer" (a reminder, in the thread)
- Slack: ":rotating_light: @here Nobody has answered" (the second message, possibly in a second channel)
- Slack: ":no_entry: Expired with no answer", and the Approve/Deny buttons are gone
- API: approving returns HTTP 409 "approval request expired with no answer"
- Executor: a skipped execution saying "the approval is N minutes old, past the 60-minute limit"

## Likely causes

1. **Nobody was watching** (night, away). The ordinary case; the reminders exist for it
2. **Slack is not delivering** (the app lost its connection, a token expired), so the buttons never arrived
3. **The proposal is not worth approving** and everyone is ignoring it: deny it, do not let it expire
4. **The executor was stopped** after approval (production is parked at night), so an approved fix went stale

## Diagnosis

- Is the problem still there? Look at the incident's signals (`make context INCIDENT=<id>`). If it
  fixed itself, nothing needs doing
- Is the gateway posting? Look for the `slack_chatops: connected` line in its log
- How old is the request? `GET /v1/actions` shows `created_at`

## Remediation

- **Still waiting:** read the proposal, then approve or deny it. That is the only thing that closes it
- **Expired:** do not look for a way to revive it. Ask for a fresh diagnosis (`make diagnose INCIDENT=<id>`);
  the new proposal is judged on how things are *now*
- **Approved but refused as too old:** the same, a new proposal and a new approval
- **Slack not delivering:** fix the Slack connection first (the Slack Secret and the app's Socket Mode);
  the reminders then resume on their own

## Do not

- Do not edit the database to un-expire a request or to write a decision by hand. The age check is the
  protection against acting on a stale judgement
- Do not raise the expiry settings to "give it more time" in the middle of an incident
