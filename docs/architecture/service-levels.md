# Service levels

What Kaval promises about its own behaviour, and how that is measured. Written before the
first chaos run so a run can pass or fail ([ADR-0041](../adr/0041-service-levels-and-recording-whether-a-fix-held.md),
`KAV-77`). Every number here is **provisional** until the first measured game day; the review
is a change to this file and an amendment to the ADR, not a quiet edit.

**Window.** The last 20 incidents, not a calendar month: production is parked most of the time
and a month of mostly nothing would flatter every number.

**Words.** *SLI* is the thing measured. *SLO* is the target for it. *Error budget* is how much
failing the target allows: a 95% objective allows 1 miss in 20.

| # | SLI (what is measured) | SLO | Measured from | Counts as failing |
|---|---|---|---|---|
| 1 | **Detect** — fault to incident opened | 95% within 120 s | Chaos runs only: the injection time is known. Real faults have no known start. Includes the correlator's 60 s grouping wait | 2 or more misses in 20 |
| 2 | **Diagnose** — incident opened to proposal filed | 95% within 90 s | `incident.created_at` → `proposal.created_at` | 2 or more misses in 20 |
| 3 | **Machine time to restore** — first signal to fix finished, minus the human wait | median ≤ 5 min, p90 ≤ 10 min | `make mttr` | median or p90 over its line |
| 4 | **Resolution** — executed fixes that held, with no regression inside 2 h | 90% | `outcome` rows | fewer than 18 of 20 |
| 5 | **Safety** — a `never`-class action executed, or an `ask`-class one without approval | 0, no budget | `execution` joined to `decision` and the policy re-check | any single one |
| 6 | **Wall-clock time to restore** — the same, human wait included | reported, **no target** | `make mttr` | never; it depends on how fast a person answers |

Notes:

- #5 is the only one that is not a percentage. It must never regress, and it is not about speed.
- #6 has no target on purpose. Quoting a target for the human's part would be inventing it; the
  honest figure is machine time next to wall-clock time. How fast a person is *expected* to
  answer is a separate rule, approval expiry and escalation (`KAV-82`).
- The `latency` and `disk-fill` experiments have a different pass condition: the right outcome is
  an alert to a human and **no automated action**. Those runs are scored on #5 and on the incident
  being opened and escalated, not on #3 and #4.
- An experiment that misses an objective is a finding, not a failure. It goes in the game-day
  write-up and either the system or the number changes, with the reason recorded.
