# ADR-0064 — The spending brake names every server group and is proven on staging

- **Status:** Accepted (plan approved by Roshan 2026-10-21). The live proof is recorded in [Lab 62](../labs/lab-62-the-spending-brake.md)
- **Date:** 2026-10-21
- **Deciders:** Roshan
- **Story:** `KAV-96` (Phase 6) · builds on [ADR-0008](0008-production-database-on-its-own-server.md), [ADR-0028](0028-prod-app-node-on-demand-and-ceiling-50.md) and [ADR-0059](0059-always-on-cost-model-and-measuring-usage-before-credits.md)

## Context

At $48 of a $50 ceiling a function (`infra/modules/budget/lambda/hard_stop.py`) scales production's server group to zero
and stops every other running `Project=kaval` server, snapshotting each data disk first. It has been armed on
production since `KAV-50` and it already runs every night at 02:00 IST. Reading it again for `KAV-96` found:

1. **A gap.** It knew one group name, production's. A server inside a group is deliberately skipped by the server-stop
   action (the group would only replace it), so a running **staging** app server was never stopped by the brake.
   Staging's database was. Staging's own four-hour idle stop was the only thing that would have ended it.
2. **No tests.** The decisions (dry-run, skip a server in a group, a failed snapshot must not block the stop) were
   written down and never checked.
3. **Never seen firing through the real path** (AWS Budgets, then the alert topic, then the function). The nightly runs
   exercise the function but not that path.
4. **The numbers repeat in four places** (Terraform, the module's defaults, the project rules, the budget plan) with
   nothing to say they must agree.

## Decision

1. **The function takes a list of group names**, `ASG_NAMES`, and the module a list `asg_names`; production passes
   production's and staging's. A group that does not exist when it fires (staging is destroyed between uses) is skipped,
   not an error. A group already at zero is left alone. Each step is tried even if another fails, and if anything
   failed the function raises at the end so the alert topic retries (every step is safe to repeat).
2. **Tests without AWS** for each of those decisions, wired into CI, the Makefile and `pyproject.toml`.
3. **A test that the ceiling says the same thing everywhere** (`scripts/dev/test_budget_wiring.py`): the lines are in
   order, the module's defaults equal production's values, the project rules and the budget plan quote them, the brake
   is armed, and it names production and staging.
4. **A live proof on staging** through the real alert topic (done twice). The further test, lowering the real budget's stop
   line so AWS Budgets itself sets the brake off, was offered and **not run** by the owner's choice; the first real crossing
   will be that link's live proof. See Lab 62.
5. **A runbook** for a brake that fired ([budget-hard-stop-fired](../runbooks/budget-hard-stop-fired.md)).

## Alternatives rejected

- **AWS Budget Actions** (the built-in way to stop servers or attach a deny policy): it cannot scale a server group, which
  is where the cost is, so the function would still be needed.
- **A scheduled spend check every few hours** that stops again after a restart: a good addition for a system left
  always-on, but a new mechanism with its own cost and failure modes. Recorded as a follow-up, not built here.
- **A notice published by the function** when it stops something: the function is itself subscribed to the topic it
  would publish to (a loop), and the Budgets alert already emails at the same threshold.
- **Matching staging by a tag instead of naming the group:** the group has to be scaled, not its servers stopped, so its
  name is what is needed; a name list is explicit and a test checks it.

## Found by the live proof (2026-10-21)

Firing the real alert path on staging worked (the group went 1 to 0, the database was stopped), and showed what the
laptop tests could not: the pre-stop snapshot was **refused**. `CreateSnapshot` is authorised against the disk and the new
snapshot, and the brake's policy covered only the disk, so neither the budget firing nor the nightly 02:00 IST run had ever
taken its extra copy. The stop is not blocked by a failed copy (by design), and the regular daily backup copies were there, so
no data was at risk. The policy now also allows creating a snapshot the request labels as the project's, and a test fails for
any module that grants one without the other.

## Consequences

- Staging's app server stops at the brake too, and at the nightly 02:00 IST run (the same function), which matches the
  nightly stop already recorded for staging in [ADR-0029](0029-staging-parks-itself-when-idle.md).
- The brake still fires once per month at the crossing; it does not stop a restart afterwards. The runbook says so.
- A change to the ceiling or its lines now has to touch four places in one change or a test fails.
- Cost: $0 (a Lambda run a night and one test run).
