# ADR-0059 — Production runs always-on from Phase 6, built in-house, and the money is measured before credits

- **Status:** Accepted for decisions 1, 2, 4 and 5; decision 3 (the budget counts usage before credits) was applied to the live budget on 2026-10-16 after its plan was shown and Roshan said go
- **Date:** 2026-10-16
- **Deciders:** Roshan
- **Story:** `KAV-91` (Phase 6) · follows [ADR-0028](0028-prod-app-node-on-demand-and-ceiling-50.md) (the $50 ceiling) and [ADR-0008](0008-production-database-on-its-own-server.md) · cost detail in [budget-plan.md](../cost/budget-plan.md)

## Context

Phase 6 teaches Kaval to read the AWS bill and find waste. The roadmap requires the cost model to be redone **before**
production is switched to always-on, because the $50 ceiling was set for a system that is parked most of the time.
Checking it turned up three facts that were not written down anywhere:

1. **Always-on is affordable.** The itemised worst case is about $41.50 with everything already built (alarm included),
   about $38 to $39 expected. Production's two servers and two public addresses cost about 4.4 cents an hour together
   (measured 2026-10-14), roughly $32 a month before disks and the small items.
2. **The account's credits hide the bill from its own guards.** The account holds $140 of credits (no card is charged
   until they are gone). Read from the live account on 2026-10-16: Cost Explorer shows **$1.39 of usage in September and
   $6.41 so far in October**, and credits cancel every cent, so the net figure is $0.00. The live budget
   `kaval-monthly` has `IncludeCredit = true`, so its actual spend also reads **$0.00**. The alerts at $42 and $46 and
   the hard stop at $48 compare against that net figure. **They would not fire until about $140 of usage had piled up.**
   `make cost-report` had the same blind spot (it also still used a ceiling of $40).
3. **The account is shared.** An ECS cluster named `stock-trader` (another project of Roshan's, with its own $5
   budget) runs Fargate tasks that add about $0.06 a day, $1.75 a month. It counts in the account's totals, and it is
   **not** Kaval's. Any cost action Kaval takes must therefore be limited to resources that are Kaval's.

## What real companies do

| Pattern | One line | Fits here? |
|---|---|---|
| Buy a cost tool (CloudHealth, Vantage, Finout) | A vendor reads the bill and shows dashboards and savings | No: a vendor and a recurring bill for one person; it recommends, it does not act through our approval path |
| Switch on AWS Cost Anomaly Detection only | AWS's own monitor sends an alert when spend departs from its learned pattern | Part of the answer, not all (comparison below) |
| A small in-house reader that tags, scans and files proposals | Cost Explorer plus resource scans, feeding the same review and approval flow as incidents | **Yes.** It is the whole point of the phase: a second domain through the machine already built |

## Comparison with AWS Cost Anomaly Detection

Facts from AWS's own FAQ and pricing pages, read 2026-10-16.

| | AWS Cost Anomaly Detection | Kaval's cost reader |
|---|---|---|
| What it looks for | **Change**: spend that departs from what its model expected | **Waste**: resources that cost money and do nothing, including ones that have cost the same every day since they were forgotten |
| Needs history | At least 10 days of data before a monitor detects anything | Reads the resources directly, so day one |
| How often | About three times a day after billing data is processed | Daily by design (Cost Explorer lags about a day) |
| Default alert level | Anomalies over 40% above expected **and** at least $100 | Per-item: a $1.82 forgotten disk is a finding |
| Output | An alert with up to 10 approximate root causes | A proposal with a preview of what would change, an approval step, an audit row, and a measured outcome |
| Action | None | Through the existing executor, an allow-list and a person's approval |
| Price | Requires Cost Explorer to be enabled; I found no separate charge on AWS's pages, but no explicit "free" either: confirm in the console before relying on it | Cost Explorer calls at $0.01 each, capped (decision 2) |

The honest summary: a forgotten 20 GB disk never produces an anomaly, because its cost never changes, so an
anomaly monitor cannot find it. That is the gap the reader fills. The two are complementary, so once its price is
confirmed in the console a monitor with a low alert level is switched on as a second opinion (done in `KAV-92`'s live
step) and what each caught is recorded.

## Decision

1. **Production goes always-on in Phase 6.** Expected about $39 a month, worst case about $42.10, against the $50
   ceiling. The switch happens at `KAV-92`'s live step, after the Terraform plan is shown and Roshan says go; the
   production-only checks parked in `KAV-90` are done at the same bring-up. Until then production stays parked.
2. **The cost reader is built in-house and its Cost Explorer calls are capped at 60 a month ($0.60 at most;
   about 35 expected).** One request per day for the breakdown by service, a small number for tags and for reports.
   The cap is a test, and a failed or missing daily pull raises its own alarm (an unread bill must not look like a
   quiet one). Its login is read-only and separate from the executor's.
3. **The budget and the report count usage before credits.** `kaval-monthly` is changed to leave credits
   and refunds out (`include_credit = false`, `include_refund = false`), so $42, $46 and $48 mean $42, $46 and $48 of
   usage, as the ceiling always meant, and the credits last as long as possible. `make cost-report` already does this
   (changed in this story). No cost. The budget is a live resource, so the change was shown as a plan (0 to add, 1 to change, 0 to destroy,
   targeted at the budget only) and applied on Roshan's go. Read back afterwards: `IncludeCredit` and `IncludeRefund`
   false, actual spend $6.412 of $50 (it read $0.00 before), and a second plan said No changes. `KAV-96`
   (arming the hard stop) builds on it.
4. **Build it, and run AWS's monitor alongside.** Not buying a tool; not relying on the anomaly monitor alone.
5. **Kaval acts only on Kaval's own things.** Every cost proposal is limited to resources carrying Kaval's project tag.
   `stock-trader` and anything else in the account is read and reported on, never proposed for change.

## Consequences

- **Cost:** no AWS spend from this story. Always-on adds, over the paused cost, roughly $34 a month (the table in
  budget-plan.md already carries it); the reader adds at most $0.60.
- **The credits are not enough for a long always-on stretch.** About $132 of the $140 is left (usage so far $7.80).
  At about $39 a month that is roughly **three and a half months of always-on, to about the end of January 2027**;
  after that the card is charged $39 a month. budget-plan.md projected only two always-on months (Feb–Mar 2027), which
  no longer matches the calendar (Phase 6 starts now). The plan: stay always-on only while a phase needs it, park
  between phases, and re-read the credit balance in the console at the start of each phase. The projection is corrected
  in budget-plan.md in this story.
- **Risk if decision 3 is not taken:** the brakes only engage when credits are nearly gone. That is a correct
  *money* position (nothing is charged) but not the position the ceiling was built for.
- **Alerts:** the first alert ($42) now sits at the top of the worst case ($42.10). It could fire when everything is
  at its busiest in the same month; that would be information, not noise. No change to thresholds.
- **Reversal:** park production exactly as before (`node.auto.tfvars`, plan, apply, pause the database).

## Measured

| What | Reading, 2026-10-16 |
|---|---|
| September usage / credits applied | $1.3896 / -$1.3896 |
| October to the 16th, usage / credits applied | $6.4119 / -$6.4119 |
| `kaval-monthly` actual spend as the budget sees it | $0.00 of $50 |
| Same month with credits left out (`make cost-report`) | $6.41: EC2-Other 2.69, compute 1.65, network 1.16, container service (Fargate, `stock-trader`) 0.58, S3 0.28, ECR 0.05, Cost Explorer 0.01 |
