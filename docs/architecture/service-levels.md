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

## First measurement: the oom game day, 2026-10-11 (staging)

One run is an anecdote, not a rate; the targets stay provisional until there are twenty
([post-mortem](../postmortems/2026-10-11-oom.md)).

| # | Result | Against target |
|---|---|---|
| 1 Detect | 81 s | within 120 s: met (1 of 1) |
| 2 Diagnose | **not measured**: staging has no model; the figure is a stand-in's | n/a |
| 3 Machine time | 158 s | median 5 min: met |
| 4 Resolution | resolved, no regression | 1 of 1 |
| 5 Safety | none executed without approval or against policy | 0 violations |
| 6 Wall-clock | 431 s, of which a person's answer was 273 s | reported, no target |

## Three runs so far (staging, 2026-10-11 and 12)

Still anecdotes, not rates: three samples, three failure types' worth of detail (memory twice, a bad release
once). A lost server is scored on its own: it is not an incident Kaval can open.

| Run | Detect (#1) | Machine time (#3) | Wall-clock (#6) | Fix held (#4) |
|---|---|---|---|---|
| oom, 2026-10-11 | 81 s | 158 s | 431 s | yes |
| badrelease, 2026-10-11 | 66 s | 94 s | 168 s | yes |
| oom, 2026-10-12 | 95 s | 157 s | 231 s | yes |

- **#1 Detect:** 3 of 3 within 120 s (81, 66, 95). Met so far.
- **#3 Machine time:** median 157 s against 5 min. Met so far.
- **#4 Resolution:** 3 fixes held out of 3, no regression.
- **#5 Safety:** nothing executed without approval or against policy.
- **#2 Diagnose** is still not measured (staging has no model; every figure is my stand-in's).
- **Lost server (not an incident):** about 2 min 10 s from ending the machine to every pod ready, no one acting
  ([post-mortem](../postmortems/2026-10-12-node.md)). During that time nothing could alert; the outside
  "still alive" check in `KAV-83` is designed against that figure.
