# ADR-0103: The app server falls back to a second size when AWS has none of the first

**Status:** Accepted (built `KAV-163`; live after the staging proof and production's apply)
**Date:** 2026-11-18
**Related:** [ADR-0028](0028-prod-app-node-on-demand-and-ceiling-50.md) (the node went On-Demand because Spot
ran out), [ADR-0004](0004-environment-strategy-and-promotion.md), [Lab 104](../labs/lab-104-a-second-size-for-the-app-server.md),
`KAV-163`, [runbook](../runbooks/prod-rollout.md)

## Context

On 2026-11-17 staging's app server could not start: AWS answered "we currently do not have sufficient
t4g.medium capacity" in `ap-south-1c`, then in every zone, and the server group failed every retry for
about an hour (the launch finally succeeded at about 10:00 IST). On 2026-11-18, production's start-up met
the same answer in all three zones at 09:18 and was still waiting an hour later. On-Demand (ADR-0028)
removed Spot's reclaim risk, but On-Demand is not a guarantee either: one size is one pool, and a pool
can be empty. With one size, every start-up of staging or production depends on that pool.

## What real companies do

1. **A prioritised list of equivalent sizes** in the auto-scaling group (a mixed-instances policy): try
   the first, fall back to the next, by itself. The common answer; costs nothing unless the fallback is used.
2. **A capacity reservation** (pay for the server whether or not it runs). Guarantees a server; costs the
   full hourly price 24 hours a day, which this project's ceiling cannot carry for a node that is parked.
3. **A second region or a second account.** Real, but a project of its own: the data, the images, the
   signatures, the state and the alarms all live in `ap-south-1`.

## Decision

1. **The node module takes a list of sizes, `instance_types`, best first.** Default:
   `["t4g.medium", "c6g.large"]`. The server group becomes a prioritised mixed-instances policy over the
   list, 100 percent On-Demand unless `spot` is on (so ADR-0028 stands).
2. **The second size is `c6g.large`:** 2 CPUs and 4 GB like `t4g.medium` (so the node and the 4 GB headroom
   figures behave the same), Graviton (arm64, so the images are right), and a different family, so a
   different pool of servers. Prices in Mumbai on 2026-11-18, from AWS's price list: `t4g.medium`
   $0.0224/hr, `c6g.large` $0.0426/hr.
3. **Production takes the module's list unchanged; staging takes it from a variable** so a proof session
   can put the second size first and show the whole system runs on it.
4. **A test over the module** (`scripts/dev/test_node_sizes.py`) fails if the list has one size, a repeat,
   a non-Graviton or unknown size, a different shape from the first, a first size other than `t4g.medium`,
   or if the group's policy is not prioritised and On-Demand.

## Cost

Nothing while `t4g.medium` is available. On the fallback, +$0.0202 an hour (about 2 cents), and only
while the node runs: a 1-hour session costs 2 cents more. If production ran on `c6g.large` all month
(730 hours) the node would cost $14.75 more; it does not, because it is parked between sessions and the
next start-up tries `t4g.medium` first. [budget-plan.md](../cost/budget-plan.md) carries the row.

## Consequences

- A shortage of `t4g.medium` no longer stops a start-up; the group launches `c6g.large` within the same
  retry. How often the fallback is used is visible in the group's scaling activity and the node's instance type.
- A running node does not swap back by itself when `t4g.medium` returns; the next start-up chooses again.
- Spot's market setting moved from the launch template to the group's distribution (AWS refuses a spot
  block in the template beside a list of sizes). With `spot = false`, as today, nothing changes.
- What this does not cover: a shortage of both sizes at once, or of the database server (a single
  `t4g.small`, which is only stopped and started, so it has not met a shortage).

## Alternatives rejected

- **`t4g.large`:** the same family may share the shortage, and it costs twice as much.
- **`m6g.medium`:** one CPU only; k3s and nine services want two.
- **A capacity reservation:** pays 24 hours a day for a node that runs for hours.
- **Another region now:** not a fix, a migration; recorded as a future-scope idea only.
- **Waiting every time:** staging's wait was an hour, production's longer; a release round and its
  proofs should not hang on a pool of servers.
