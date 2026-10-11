# ADR-0041 — Service levels, and recording whether a fix held

- **Status:** Accepted — built, tested against a real Postgres; proven live on the dev server ([Lab 44](../labs/lab-44-recording-whether-a-fix-held.md)) and on staging with real database roles ([Lab 45](../labs/lab-45-watching-the-watcher.md))
- **Date:** 2026-10-11
- **Deciders:** Roshan
- **Story:** `KAV-77` · first slice of Phase 5 (`KAV-10`) · uses the `outcome` table from the original spine

## Context

Phase 5 turns "Kaval fixes problems" into a measured claim. Two things were missing before any
fault can be injected:

1. **Nothing wrote the `outcome` table.** It has existed since the first migration
   (`resolved`, `mttr_sec`, `regression`), and its docstring says "measured 5 minutes after
   execution", but no program did the measuring. Time to restore had no end point.
2. **No target said what "good" is.** A chart of recovery times with no line on it cannot say
   whether a run passed. The Phase 5 plan asks for the service levels *before* charting.

## Decision

### Service levels are written down first

[Service levels](../architecture/service-levels.md) defines six indicators (detect, diagnose,
machine time to restore, resolution rate, safety, and wall-clock time reported without a target),
the objective for each, and what counts as failing. They are provisional: set from the Phase 5
learn page's steady state, reviewed after the first measured game day, changed only by an
amendment to this ADR. The window is "the last 20 incidents", not a calendar month, because
production is parked most of the time and a month of mostly nothing would flatter every number.

### One program decides "did it hold", five minutes after the fix

`kaval_executor.verify` runs after each executor pass. For every incident with a successful
execution that has aged five minutes:

- **Resolved** means no new problem signal for that workload in the five minutes after the fix
  finished. The evidence is silence from the same sources the correlator uses, judged by the
  correlator's own `subject()` and `cause_of()`, so "the same problem" means the same thing in both.
- **Not resolved** is written as a row saying so. Nothing retries; a person or the next incident does.
- **Regression**: a fix judged resolved, followed within two hours by a new incident with the same
  fingerprint, gets a second `outcome` row (`resolved=false, regression=true`). A fix that looked
  good for ten minutes is not counted as a success forever.
- `mttr_sec` is **wall-clock**: the incident's first signal to the moment the fix finished,
  human approval included. It is stored once and never recomputed.

Only Kubernetes incidents are judged. AWS cost incidents are daily data about a day already
over, so five minutes proves nothing; Phase 6 gives them their own check.

### The report separates machine time from human time

`make mttr` (`kaval_executor.mttr`) measures "incident opened" from the row's `created_at`, not
`opened_at` (which is the first signal's own time; the live run first printed `detect 0s` for this
reason). It splits each wall-clock figure into stages that add up exactly
(detect, diagnose, human, queue, execute) and prints median, p90 and worst, for both machine time
and wall-clock. It never prints a mean, and never only the flattering number. Incidents that
later regressed still appear, because they were resolved when measured; the resolution-rate
indicator is where regressions count against the system.

## Alternatives considered

| Alternative | Why not |
|---|---|
| A separate verifier Deployment with its own database role | The textbook answer: the thing that fixes should not mark its own homework. Rejected *for now* because it needs a fifth database role and secret in every environment for a 100-line function that only reads telemetry the executor never writes. The check is independent in the way that matters (it reads the signals other components wrote, not the executor's own claim). **Switch to a separate role if the verifier ever needs more than read-only signals**, or if the executor gains a second replica |
| Measure by asking the cluster ("is the pod Ready?") | Needs Kubernetes read access in the verifier and measures the wrong thing: a pod can be Ready and still be killed again in a minute. Silence from the detectors is what the incident was opened on, so it is what closes it |
| Compute MTTR only in the report, store nothing | The `outcome` row is also the evidence the autonomy argument needs (ADR-0017: no action is promoted to `auto` without outcome history), and the gateway can already read it |
| Mean time to restore | Durations are skewed; one two-hour incident disappears inside a 4-minute average |
| A calendar-month SLO window | Production is parked most of the time; see above |

## Security

The executor's database role gains **read-only** access to `signal`, `incident`,
`incident_signal` and `proposal` (`scripts/ops/db-roles.sql`). That is telemetry and the agent's
own output, no secrets, and the executor already holds the cluster's only write permissions, so
the new read widens nothing an attacker would want. The verifier never touches Kubernetes or AWS
and a failure in it is printed and dropped, never allowed to stop an execution.

## Failure and rollback

- A verification error prints `verify failed: …` and the next pass retries; execution is unaffected.
- A crashed executor simply verifies late; nothing is lost because the decision is derived from
  rows that stay.
- Rollback is reverting the commit. The extra `outcome` rows are inert history; the grants are
  harmless to leave in place.
- **Not covered here:** nothing alerts when the verifier stops. That is the watch-the-watcher
  story (`KAV-78`).

## Consequences

- Needs the grants applied to each environment's database. Each node re-applies `db-roles.sql`
  from `main` on every boot and every six hours ([ADR-0027](0027-automatic-secret-and-role-recreation.md)),
  so merging is what delivers them. Until they land, the verifier prints
  `verify failed: permission denied` once a pass and everything else works.
- `make verify-outcome` pointed at a script that never existed; it is removed and replaced by `make mttr`.
- The first real numbers arrive with the first game day (`KAV-79`), at which point the objectives
  are reviewed against data, not guesses.
