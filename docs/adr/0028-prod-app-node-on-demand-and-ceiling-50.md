# ADR-0028: The prod app node is On-Demand, and the cost ceiling rises to $50

**Status:** Accepted
**Date:** 2026-10-08
**Related:** [ADR-0008](0008-production-database-on-its-own-server.md) (the previous ceiling raise,
$25 to $40), [ADR-0024](0024-prod-landing-network-ecr-iam-node.md) (the Spot node this changes),
[ADR-0027](0027-automatic-secret-and-role-recreation.md) (rejected On-Demand for a different
question), [ADR-0004](0004-environment-strategy-and-promotion.md), `KAV-58`, Labs 28 and 29

## Context

The app node was Spot by design: a `t4g.medium` at ~$0.0105/hr instead of $0.0224/hr, a saving
that mattered when the whole project had a $25 ceiling. Spot has one failure mode that a database
or a demo does not forgive: **no capacity, so no node.** It happened on 2026-10-06 (Lab 28:
`t4g.medium`, then `t4g.large`, in all three AZs for hours) and again on 2026-10-08 (Lab 29,
staging). Both times an On-Demand request for the same instance type launched immediately.

The ASG's node replacement already makes a *reclaimed* Spot node survivable (ADR-0027). It does
not help when AWS has nothing to give.

## Decision

1. **The app node is On-Demand by default** (`infra/modules/node`'s `spot` variable now defaults
   to `false`). Staging is On-Demand too. `spot = true` stays available for anyone who wants the
   saving back and accepts the risk.
2. **The ceiling rises from $40 to $50.** Alerts at **$42** and **$46**, hard stop at **$48**
   (were $30 / $35 / $38).

## The arithmetic

| | Spot (before) | On-Demand (now) |
|---|---|---|
| App node compute, 730 h | ~$7.80 | $16.35 (+$8.55) |
| Everything else always-on | unchanged | unchanged |

Rates from the AWS Price List API for ap-south-1, verified 2026-09-26 and 2026-10-06
(`t4g.medium` On-Demand $0.0224/hr).

**A correction found while doing this.** The steady-state table in `docs/cost/budget-plan.md`
labelled "app node subtotal ~$13" counts only compute, disk and IPv4. The rows listed above it
for ECR, S3, logs, Bedrock, Jev and data transfer (about $3.75/month) were never added in, so the
"~$27 total" understated the always-on figure. Counting everything the table lists, steady state
was already **~$31.70 on Spot and is ~$40.25 on On-Demand at worst** (Bedrock, Jev and transfer
at their "light use" ceilings), roughly $38–39 expected. That is why the first alert is $42 and
not the $40 the old spacing would give: an alert that fires on normal spend trains you to ignore
it, which is the reason the old first alert sat just above steady state.

## What limits the bill in practice

The always-on figure only applies when it is always on. `make down` and the 02:00 IST nightly
auto-stop (ADR-0008) already keep compute off outside working sessions, and the $48 hard stop
sits $7.75 above the worst-case steady state. In paused months the premium is the ~$0.012/hr
difference for the hours actually run, a few cents per session.

**Effect on the AWS credit.** The $140 credit expires 2027-09-11. Always-on from Phase 6 to 8 is
about seven weeks (~1.6 months), so roughly +$14, plus about $2 of premium across the paused
months: the project projection moves from ~$110 to ~$126. That leaves roughly **$14 of headroom
instead of ~$30** to absorb a mistake. This is the real cost of the decision and it is accepted
with eyes open.

## Alternatives considered

- **Stay on Spot, add a fallback (`mixed_instances_policy`).** The ASG could try several instance
  types, or hold an On-Demand base. It cuts the premium and keeps most of the reliability, but
  it is new moving parts, and both shortages hit the whole `t4g` family at once. Still worth
  revisiting if the credit headroom gets tight; not built.
- **Stay on Spot and wait.** What Lab 28 did, for hours. Reliability should not depend on AWS's
  spare capacity for a node whose demo is the point.
- **`t4g.small` On-Demand.** ADR-0004: 2 GB cannot hold the model alongside k3s. Rejected there.
- **Raise the ceiling only for always-on phases.** The thresholds are Terraform variables, so
  this is possible later; a single figure is simpler to reason about and to state.

## Consequences

- Prod's launch template loses its `instance_market_options` block and the budget module's
  thresholds change, both in-place, on the next prod apply (shown, then approved, before it runs).
- Every living statement of the ceiling and thresholds is updated in the same change
  (`CLAUDE.md`, `budget-plan.md`, `README`, the dashboard, Terraform defaults). Dated ADRs, labs
  and journals keep the figures that were true on their day.
- **Revisit if:** the credit headroom drops below roughly $10, if Spot capacity proves reliable
  for a stretch (the premium is then hard to justify), or when Phase 7's always-on posture starts.
