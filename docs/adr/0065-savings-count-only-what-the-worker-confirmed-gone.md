# ADR-0065 — Savings count only what the worker confirmed gone, and the report says it is an estimate

- **Status:** Accepted (plan approved by Roshan 2026-10-22). The live proof is recorded in [Lab 63](../labs/lab-63-savings-tracking-and-the-phase-6-exit-test.md)
- **Date:** 2026-10-22
- **Deciders:** Roshan
- **Story:** `KAV-97` (Phase 6) · builds on [ADR-0062](0062-waste-is-found-daily-by-a-read-only-scan-and-reported-to-a-person.md) and [ADR-0063](0063-waste-is-removed-in-steps-by-a-separate-worker-and-only-with-approval.md)

## Context

Kaval now finds waste, asks a person twice, and deletes it through a separate worker (`KAV-94`, `KAV-95`). The
phase's claim is that this *saves money*, and nothing yet adds that up. Two facts were already recorded, in two
places that never meet:

1. **What a proposal promised** was written only in its wording ("Saves $1.82 a month"). A sentence cannot be summed.
2. **What the worker did** comes back as a `removal_done` (or `removal_failed`) signal that names the monthly amount the
   deleted disk stopped costing. Backup copies carry no amount: their cost depends on what they share with other copies.

A savings report also has a standing way to lie: counting an opportunity as money saved. FinOps practice keeps
*identified*, *approved* and *realised* apart for that reason.

## Decision

1. **Store the promise as a number.** The planner writes `saving_usd_month` into the delete step's `params` when the
   scan gave a well-formed monthly cost; otherwise it writes nothing. No database change (`params` is already JSON).
   Missing or malformed means "no price", never zero.
2. **Derive the report, do not store a second copy.** `kaval_executor.savings` (`make savings-report`, `make staging-savings`)
   reads proposals, decisions and the worker's answers and builds the report on demand. A saved total could drift
   from the facts it came from; a derived one cannot. It is read-only.
3. **Five states for each thing proposed for deletion:** `confirmed`, `approved`, `waiting`, `declined`, `expired`.
   Only `confirmed` is *realised*, and only if a person said yes **and** the worker answered `removal_done` after the
   question was asked. A "done" with no yes behind it, an answer to an older question, a failed removal and another
   disk's answer are never counted.
4. **One row per thing.** If a question expired and was asked again, the furthest state is shown and the promise is
   counted once.
5. **"Realised" means a confirmed monthly amount, priced from the scan's table (Mumbai gp3), plus the days since it was
   confirmed.** It is an estimate and the report says so every time. It is not read off the AWS bill: a 9-cent disk
   cannot be seen in a bill, and pretending to would be false precision.
6. **Backup copies are listed and counted but carry no amount** until there is an honest way to price an incremental
   snapshot. They appear as "no price" in the counts, never as $0.
7. **The cost proposals line is closed by `KAV-95`,** which already built the approve-then-execute path for cost
   findings; this story adds only the accounting.

## Alternatives rejected

- **A new `savings` table.** Two truths to keep in step (the rows and the table). Rejected for a derived report.
- **Reading the AWS bill for "realised".** The right answer for large changes, wrong for cents; Cost Explorer lags a day,
  costs a cent a call, and cannot attribute a single disk. Revisit if Kaval ever proposes something big (a server type).
- **Counting `approved` as saved.** An approval is a decision, not a result; the worker can still refuse or fail.
- **Pricing backup copies at $0.05 a GiB-month.** Snapshots are incremental, so the real saving is at most that and
  often far less. A number we cannot defend is worse than "no price".
- **Adding a Prometheus metric for savings.** The decision has no alert to hang on it yet; a metric nobody alerts on is
  noise. The report is the visibility. Revisit with the always-on posture.

## Found by the live proof (2026-10-22)

On staging the planner filed a second step-one question for the same disk 12 seconds after the first was approved: it held off
asking again only once the executor had written its record. The pause now starts at the yes (PR #236, with a test that fails
without the fix). The report itself read the real answer correctly the first time: promised $0.09, confirmed $0.09 ([Lab 63](../labs/lab-63-savings-tracking-and-the-phase-6-exit-test.md) Part 4).

## Consequences

- Deployed with the normal release: the agent image carries the planner change, the executor image carries the report.
  Production picks both up at its next bring-up; until then a production disk proposed *before* that change has no stored
  promise and shows as "no price" (it still counts as confirmed, with the worker's own amount).
- The report is a command a person runs; nothing pushes it. A weekly line in the journal is the habit, until a dashboard
  panel earns its place.
- $0 a month. No new infrastructure, no new permission: it reads tables the executor already reads.
- Follow-up recorded, not built: price backup copies honestly; a repeating always-on view of the same numbers.
