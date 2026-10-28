# ADR-0066 — Production's first real Slack post comes from one real planted disk, and production gets a read-only smoke test

- **Status:** Accepted (plan approved by Roshan 2026-10-26). Run on 2026-10-27 and 2026-10-28; the live proof is [Lab 64](../labs/lab-64-closing-phase-6-on-production.md)
- **Date:** 2026-10-26
- **Deciders:** Roshan
- **Story:** `KAV-90` (Phase 6 production checks); also unblocks `KAV-92` criterion 5 and `KAV-98` criterion 4 · builds on [ADR-0044](0044-chaos-with-our-own-jobs-and-a-bounded-memory-fix.md), [ADR-0062](0062-waste-is-found-daily-by-a-read-only-scan-and-reported-to-a-person.md), [ADR-0063](0063-waste-is-removed-in-steps-by-a-separate-worker-and-only-with-approval.md)

## Context

Phase 6 is built and proven on staging. What is left is five checks that only production can answer
(`KAV-90`). Three of them need a decision first:

1. **A real Slack post from production.** Staging has no Slack app, so nothing has yet been posted by
   production's own gateway about a real finding. The checklist says the way it is provoked is chosen in an ADR.
2. **Production's time to restore.** The checklist wants it recorded "if a real fix happened".
3. **A formal production smoke test.** `staging_smoke.py` exists and is the gate for promotion
   ([ADR-0031](0031-staging-smoke-test-and-the-passed-staging-record.md)); nothing equivalent checks production once
   it is up. The 2026-10-21 bring-up was checked by hand commands.

The rule written into the rollout runbook is *"do not create a pretend incident in production's database to see a
Slack post; a real post waits for a real incident"*. Production is parked almost all of the time, so a real
incident may not come for weeks, and the checklist (and the phase) would stay open for as long.

## Decision

### 1. Provoke the post by planting one real, tiny, clearly named thing for the real pipeline to find

Create one real **1 GiB disk** in the production account, attached to nothing, tagged `Project=kaval`,
`Env=prod`, `ManagedBy=manual` and named to say what it is. Then let production do what it always does: the daily
waste scan sees it on two scans in a row, the collector turns it into an incident, the planner files the removal
question, and the gateway posts it to Slack. Nothing is written to production's database by hand. Roshan answers
**step one only** (take a backup copy and mark the disk, reversible); step two (delete) is never approved, and
production's wait before it (168 hours) would not allow it yet anyway. The disk is deleted by hand afterwards, with
the exact command shown first, and the backup copy later.

| Alternative | Why not |
|---|---|
| Wait for a real incident | Honest, and unbounded. Production runs a few hours a month, so the phase could stay open for weeks |
| Write a pretend incident row into production's database | The thing the runbook forbids. It puts a record in the system of record that is not true, and it would skip the scan, the correlator and the planner: it would prove the Slack token and nothing else |
| Break something real in production (chaos) | Forbidden by [ADR-0044](0044-chaos-with-our-own-jobs-and-a-bounded-memory-fix.md): chaos is off in production by structure, and it should stay that way |
| Post a test message to the Slack channel directly | Proves the token, not production's own path from finding to message. Not what the checklist asks |
| **Plant one real disk (chosen)** | A real resource, found by the real scan, through the real planner and gateway, with a person answering. The only invented thing is the disk itself, which costs about 9 cents a month and lives for a few hours |

**Fences.** The disk is 1 GiB, we create it ourselves, it carries our own tags, and the removal worker acts only
on resources tagged `Project=kaval` with a known `Env` ([ADR-0063](0063-waste-is-removed-in-steps-by-a-separate-worker-and-only-with-approval.md)).
The agent and the planner gain no new permission and the executor's write rights are unchanged. If the post does not
arrive, nothing has been broken: the disk is deleted by hand and the checklist item stays open.

The runbook line is corrected to match: planting one real resource for the scan to find is allowed; writing
pretend rows is still not.

### 2. Production's time to restore stays a staging number

Production is not broken on purpose ([ADR-0044](0044-chaos-with-our-own-jobs-and-a-bounded-memory-fix.md)), and no
real production fix is expected in this run, so there is no production restore time to record. The number Kaval
publishes (`make mttr`, [Lab 44](../labs/lab-44-recording-whether-a-fix-held.md); the game days in [Lab 46](../labs/lab-46-chaos-and-the-memory-game-day.md) to [Lab 48](../labs/lab-48-chaos-disk-full-and-a-slow-service.md)) is a staging figure, and the docs say so and
say why. If a real fix ever happens in production, it is recorded the same way, from the outcome rows.

### 3. A read-only production smoke test

`scripts/release/prod_smoke.py` (`make prod-smoke`) reuses the five staging checks against production's own
namespace and adds two:

- the images production runs are the ones that passed staging: ECR's digest for production's tag equals the digest
  on `deploy/promotion/passed-staging.json`. With the existing "what runs equals what ECR holds" check, that is
  *nothing reaches prod without passing staging* read back from the live system;
- the server's timers are set (the nightly dump at 19:30 UTC / 01:00 IST, the cost reader's copy step, the
  heartbeat), so a production that is up but would quietly stop being backed up fails.

It also prints the memory still available and whether the model answers, as notes. They are not gates: the model is
allowed minutes to load, and available memory dips to about 800 MB while it is loaded ([ADR-0055](0055-the-model-runs-in-the-cluster-and-diagnoses-new-incidents.md)).

It changes nothing and records nothing (no write to the record file), refuses to run (exit 2) when production is
parked, and the remote command is tested to contain no verb that writes, starts, stops, scales or deletes. Proven
refusing on the parked production the day it was written.

### 4. Run it staging first, and park by the nightly brake

Production's run is preceded by a staging run of the exact build (which also boots the newer server image on
staging first), and ends overnight so the 01:00 IST dump timer fires by itself with production up. Production is
then parked by the 02:00 IST brake ([ADR-0064](0064-the-spending-brake-names-every-server-group-and-is-proven-on-staging.md)),
not by hand, which is also the brake's first real run on production. Cost is about 70 cents.

## Consequences

- Phase 6 can close on evidence from the real system, with the one invented thing (a 1 GiB disk) named and priced.
- The smoke test is a new gate-shaped tool but is not wired into `promote.yml`: production is parked at promotion
  time, so there is nothing to test. It is a step of the bring-up runbook.
- `staging_smoke.py` is changed in two small, compatible ways (`node_id` and `on_node` take the environment and the
  script as optional arguments), and its "not covered" text no longer says no environment deploys the model, which
  stopped being true when staging switched it on (older records keep the sentence they were written with).
- A planted disk is a real cost and a real object; it must be deleted. The lab records the id and the deletion.

## What the run showed (2026-10-27/28, [Lab 64](../labs/lab-64-closing-phase-6-on-production.md))

- The planted disk worked as designed: the real scan found it, production's own planner filed the step-one question,
  and its gateway posted it to Slack about two minutes after the copy step ran; the 30-minute "nobody has answered"
  alert fired by itself. Nobody answered, which the check does not need. The disk was deleted by hand afterwards.
- The overnight run did what ADR-0064 promised: the dump timer fired at 01:00 IST on its own, the 02:00 IST brake parked
  production after a pre-stop snapshot, and the daily dump check passed.
- Three findings, none a reason to reverse the decision: production's two gateway copies send the same reminder twice
  (its own story); a question left open when production is parked outlives the server that could close it; and the
  Approve button's path into production's gateway has still not been pressed (staging only).

## What would make us revisit

A second production resource type worth provoking (an address, an old backup copy) would be planted the same way. A
real production incident would replace the planted disk as the evidence and be recorded instead.
