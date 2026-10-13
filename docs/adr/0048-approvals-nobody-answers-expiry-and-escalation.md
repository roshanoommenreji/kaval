# ADR-0048 — Approvals that nobody answers: a reminder, a louder message, an expiry

- **Status:** Accepted — unit-tested and proven against a real database; the live staging proof is
  recorded in [Lab 49](../labs/lab-49-people-and-process.md) (the real Slack messages are seen in prod only,
  `KAV-83`)
- **Date:** 2026-10-13
- **Deciders:** Roshan
- **Story:** `KAV-82` (Phase 5, slice 6) · roadmap line "Approvals that nobody answers"

## Context

Until now an approval request waited in Slack for ever. Two things are wrong with that. A person who
is asleep or away is never reminded, and, worse, a proposal is a judgement about how the cluster
looked *when it was written*: a click a day later would apply that judgement to a cluster the
proposal never saw. Real on-call practice has a fixed shape for this: remind, then escalate to a
second person or channel, then stop waiting. This ADR gives Kaval the same shape, and it answers the
one question the service-level table left open ("how fast is a person *expected* to answer?", row 6).

Measured so far (three game days): the slowest human step was 273 s, the others 74 s.

## Decision 1 — three steps, counted from when the proposal was filed

| After | What happens |
|---|---|
| 15 min | A reminder in the original message's thread |
| 30 min | A louder message with an `@here`, to a second channel if one is set (`SLACK_ESCALATION_CHANNEL_ID` in the Slack Secret), otherwise the same channel |
| 240 min (4 h) | The request **expires**: an "expired, nothing was done" message, and the buttons are taken off the original |

Counted from `action.created_at`, not from when Slack was told: staleness is about the proposal's age.
All three are chart settings (`approvals.*`, rendered as `APPROVAL_*_MINUTES`); they must satisfy
`0 < reminder < escalate < expire` or the gateway ignores all three and uses 15 / 30 / 240 (a
half-valid set could expire a request before it ever warned anybody; `inf` and `nan` are rejected,
because "never expires" is exactly what this removes). Staging uses 3 / 7 / 15 so a test can watch
the whole sequence; prod keeps the defaults.

A gateway that was down for hours sends only the **latest** due step, not three late messages.

## Decision 2 — the expiry is enforced where the decision is written, not where people are told

`decisions.record_decision` refuses an approve **or** a deny on an expired request
(`DecisionOutcome.expired`; the HTTP API answers 409 "approval request expired", Slack replies
"ignored — expired"). It computes this from the action's own age, so it holds when the notifier is
down, when Slack is not configured (staging), and whatever the notifier did or did not record. The
notifier only *tells people*; it cannot extend, re-open or approve anything. An expired request is
final: a fresh proposal is needed.

Why deny is refused too: nothing is waiting any more, and a late "deny" would write an audit row that
reads as a considered decision about a proposal nobody was still looking at.

## Decision 3 — an approval is also permission for *now*: the executor refuses a stale one

Production is parked most nights. If a request is approved and the executor is then stopped, it would
act the next morning on a cluster nobody looked at. The executor therefore refuses to act on a human
approval older than 60 minutes (`EXECUTOR_APPROVAL_MAX_AGE_MINUTES`; unusable values fall back to 60),
recording a skipped execution with the reason, as it does for every other refusal. Auto-approved
actions (decided by the policy engine at the moment of acting) never go stale. A refusal here is
permanent for that action (one execution row per action), which is the point: a new proposal and a new
approval are needed.

## Decision 4 — problems that are only for a person escalate too, after two hours

A full disk or a slow service has no buttons, so there is nothing to "answer" and no acknowledge
mechanism. The rule is: a fault still being reported after 2 hours (reminders every 30 minutes; a gap
of an hour or more starts a new episode) gets one louder `@here` message to the escalation channel.
"Still firing" stands in for "nobody fixed it". An Acknowledge button is not built.

## Rejected

- **Auto-approve (or auto-deny) on timeout.** An approval is a human decision; a timer must never make
  one. Expiry only removes the ability to decide.
- **Re-proposing automatically when a request expires.** The agent already proposes again if the
  incident is still open and a diagnosis is run; an automatic loop would turn a missed page into a
  stream of fresh requests.
- **No expiry, reminders only.** That keeps the stale-judgement problem.
- **Storing the deadline in a column.** A computed rule needs no migration for the rule itself and
  cannot drift from the settings. (A column for the Slack message handle, `action.slack_ts`, was
  added, only so the buttons can be removed.)
- **A second *person*.** There is one operator today. The escalation channel is the place a second
  person would be added; the design needs no change when there is one.

## Consequences

- **Found while building it:** the gateway's database role could read `action` but not write to it, yet
  the Slack notifier updates `action.slack_notified_at` after posting. In production the post would
  have succeeded and the bookkeeping update been refused, so the same approval would have been posted
  again every 30 seconds. Never seen, because Slack has not run against the real roles. Fixed with a
  column-level grant (two columns, not the table) and a test that runs the real `db-roles.sql` and
  checks what each role may and may not do (`services/shared/tests/test_db_roles.py`).
- A request that expires leaves the incident open; the next diagnosis files a new one. Nothing is
  lost, nothing runs.
- Staging has no Slack, so the messages are shown there by running the real notifier as a dry run
  inside a rolled-back transaction (`make approvals-report`), and the live part is the real gateway
  refusing a late approval. Real delivery is seen in prod (`KAV-83`).
- Not built: an Acknowledge button on people-only messages; a second person; approvals from the
  mobile app (its roadmap line already requires "the same expiry and escalation rules as Slack").
