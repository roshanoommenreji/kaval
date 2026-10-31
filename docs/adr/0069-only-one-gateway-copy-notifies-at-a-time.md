# ADR-0069 — Only one gateway copy notifies at a time

- **Status:** Accepted (plan approved by Roshan 2026-10-31). Built and tested against a real Postgres; the live proof (two gateway copies on staging, then production) is still to come
- **Date:** 2026-10-31
- **Deciders:** Roshan
- **Story:** `KAV-100`, found live in production on 2026-10-27 ([Lab 64](../labs/lab-64-closing-phase-6-on-production.md)) · builds on [ADR-0048](0048-approvals-nobody-answers-expiry-and-escalation.md) (the reminders) and [ADR-0026](0026-slack-chatops-and-deferred-mobile.md) (the notifier loop)

## Context

Production runs two copies of the gateway for availability, and each copy runs the notifier loop: post the approvals nobody has seen yet, send the reminders, escalations and expiries, and post Kaval's own health alerts. Every one of those passes does the same two things in order: ask the database "was this message already sent?", then post it and write the record. With nothing between the two steps, two copies that ask within the same second both hear "no" and both post. On 2026-10-27 the "nobody has answered" alert appeared twice in the same minute.

## Decision

**A Postgres advisory lock around one round of the notifier loop.** A round takes the lock with `pg_try_advisory_lock`; a copy that cannot get it skips the round and tries again at the next poll. The lock covers all three passes, so the first post of a new approval (which has the same gap) is covered too. It is held on a connection of its own, because a session returns its connection to the pool each time it commits and the passes commit after every message. The database gives the lock back by itself if the holder's connection ends, so a crashed gateway cannot leave it held. Code: `kaval_gateway/notifier_lock.py` and `slack_chatops.notify_round`.

## What real companies do, and why this one

1. **A lock so one worker does the job at a time** (advisory locks, leader election): chosen. No schema change, covers every pass, and fails safe.
2. **Claim the message in the database before sending it, under a unique key.** Stricter, but this project's `notification` table legitimately repeats a key (a health alert repeats every 30 minutes), so it needs a partial unique index and a way to undo a claim when Slack is down. More moving parts for the same result here.
3. **Run the notifier as a single copy.** A different deployment shape for one loop, and then it has no standby.

## Consequences

- **No schema change, no new permission.** The lock is a number the gateway's own database role can already take.
- **One window remains, as before:** if a copy crashes after posting and before recording, the next round can post that one message again. Closing it needs option 2; the window was there before and is much smaller than the one fixed.
- **A hung copy could silence notifications.** If a copy holds the lock while its Slack call hangs, the other copy skips rounds. The lock is held for one round only, Slack's client has its own timeout, and a test proves the lock is released when a pass fails.
- **Seen in the logs:** a skipped round logs `another gateway copy is notifying`. `make approvals-report` lists any approval message recorded twice, which is how a duplicate would show on staging.
- **A question still open when production is parked** is not changed: at the next start it gets the ordinary "expired, nothing was done" message (ADR-0048), because a proposal older than its expiry is no longer trusted. Written into the runbook.

## Testing

Two gateway copies are simulated with two threads and a Slack that is slow on purpose. Without the lock they send the message twice (this is the bug, kept as a test); with the lock, once, and the other copy stands aside. Also: a copy that stood aside sends nothing and a later round still works; the lock is free again after a round and after a round that failed.
