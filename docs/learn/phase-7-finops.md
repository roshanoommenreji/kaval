# Phase 7 — FinOps

> **Written from:** theory
> **Lab:** to be written
> **Cost:** posture changes to **always-on**, ~$14/mo — this phase requires it

## Where this sits

Phase 6 proved the reliability half works. This phase adds the second signal domain through the
same machine: instead of "something broke," the input is "something is wasting money."

It unlocks: the resume claim stops being about Kubernetes and starts being about the thing every
engineering organisation currently has a problem with.

## What we're doing

- Ingesting **Cost Explorer** data, and the **CUR** if depth requires it
- A **tagging strategy**, and enforcement of it
- Waste detection — idle nodes, orphaned volumes, unattached IPs, forgotten snapshots
- Cost proposals through the same policy, approval and execution path as incidents
- Wiring the Phase 0 hard-stop Lambda to the live ASG and arming it

## Why this way

**This is the phase that requires always-on, and that is a fact about the data, not a preference.**
Cost Explorer reports at daily granularity with roughly 24 hours of lag. An account that only
exists during work sessions produces no meaningful cost signal — there is nothing idle, because
nothing runs long enough to be idle.

It is also the phase where the architecture pays off. The agent, the policy engine, the approval
flow and the audit trail were all built for incidents. A cost finding is just another `signal`
row producing another `proposal`. **Adding an entire second product domain should be mostly
configuration**, and if it is not, the abstraction was wrong.

---

## Key concepts

### Cost Explorer versus the Cost and Usage Report

Two different tools, and choosing wrongly wastes a lot of time.

**Cost Explorer** is an API and console for aggregated cost data. Daily or monthly granularity
(hourly available at extra cost), grouped by service, tag, account or region. Queryable
immediately, and **each API call costs $0.01** — a detail that surprises people who build a
polling loop.

**CUR** (Cost and Usage Report) is the complete line-item dataset delivered to S3, typically as
Parquet, updated a few times a day. Every line item, every resource, every discount applied. It is
the authoritative source and it is large — millions of rows for a modest account.

For this project Cost Explorer is right: the account has a handful of resources and a $25 ceiling.
The CUR is the correct answer at organisational scale, usually queried through Athena. Knowing
which is which — and that CUR exists at all — is the differentiator in a FinOps conversation.

### Granularity and lag, and what they permit

Cost data is **not real-time**. Usage typically appears within 24 hours; final charges settle over
days as discounts and credits are applied.

Two consequences the agent must be designed around:

- **Today's spend is always an underestimate.** Alerting on it will under-fire.
- **The rate of change is more useful than the absolute figure.** "Daily spend rose 40% three days ago and has not come back" is actionable; "MTD is $11.40" is not, on its own.

This is exactly why the Phase 0 budget uses both ACTUAL and FORECASTED notifications. Forecast
compensates for lag.

### Tagging, and why untagged spend is unattributable

A **tag** is a key-value pair on a resource. Cost allocation tags must be **activated** in the
billing console before they appear in cost data — a step people miss, then wonder why grouping by
tag returns nothing.

The critical property: **tagging is not retroactive.** Activate a tag today and you get tag-grouped
data from today. Historical spend stays unattributed forever. This is why a tagging strategy has
to exist before the resources do, and why this project has `Project`, `ManagedBy` and `Purpose`
tags in the Terraform default tags from Phase 0.

A workable minimum for any account:

| Tag | Answers |
|---|---|
| `Project` | Which effort does this belong to? |
| `Environment` | prod, lab, dev |
| `Owner` | Who do I ask before deleting it? |
| `ManagedBy` | terraform, manual, helm — i.e. is deleting it safe? |

`ManagedBy` is the underrated one. It tells you whether a resource will be recreated by automation
if you remove it, which is the first question to ask about anything unexpected.

Enforcement options, roughly in order of strength: Terraform `default_tags` (covers everything
Terraform makes), AWS Config rules that flag non-compliant resources, and Service Control Policies
that refuse creation without required tags.

### The categories of waste

Cloud waste is boringly consistent, which is what makes automated detection viable.

| Category | Example | Detection |
|---|---|---|
| **Idle** | Node at 4% CPU for a week | Utilisation metrics over a window |
| **Orphaned** | EBS volume with no attachment | Resource state |
| **Oversized** | 8 GB instance using 1.5 GB | Utilisation versus provisioned |
| **Forgotten** | Snapshots from a project that ended | Age plus tag absence |
| **Untagged** | Spend nobody can attribute | Cost grouped by tag, "no tag" bucket |
| **Wrong tier** | Everything in S3 Standard | Access frequency versus storage class |
| **Zombie** | An idle NAT Gateway, an unused ALB | Cost with no corresponding traffic |

The last row is the most expensive in practice. A NAT Gateway costs $32/month whether it passes a
byte or not — which is exactly the trap this project's architecture avoids by design.

### Rightsizing, and the honest caveat

**Rightsizing** is matching provisioned capacity to actual need, and it is the highest-yield
optimisation in most accounts because over-provisioning is the default behaviour of everyone
building under time pressure.

The caveat worth stating in any interview: **utilisation metrics are not the whole story.** An
instance at 5% CPU may be memory-bound, or bursty in ways daily averages hide, or provisioned for
a quarterly peak. This is precisely why the recommendation goes to a human rather than executing
automatically — the agent sees CPU, not intent.

Note also that **CloudWatch does not report memory utilisation by default.** Memory is not visible
to the hypervisor; it requires the CloudWatch agent inside the instance. A rightsizing
recommendation based on CPU alone is incomplete, and knowing that is a good sign in a candidate.

### Commitment-based discounts

Not used by this project — the spend is far too small — but you will be asked about them.

- **Savings Plans** commit you to a dollar-per-hour spend for one or three years, in exchange for a discount. *Compute* Savings Plans are flexible across instance family, size and region; *EC2 Instance* Savings Plans are cheaper but narrower.
- **Reserved Instances** are the older mechanism, committing to specific instance attributes. Largely superseded by Savings Plans for compute.
- **Spot** is not a commitment at all — it is a discount for accepting interruption, which is what this project uses.

The mental model: **commit to what you are certain you will use, spot the interruptible parts,
on-demand the unpredictable remainder.** Over-committing is worse than not committing, because you
pay for the commitment whether or not you use it.

### Unit economics, and why the number matters

Total spend is a weak metric because it grows with success. **Cost per unit of value** — per
request, per customer, per incident processed — is the one that reveals whether efficiency is
improving.

For Kaval, the natural unit is **cost per incident processed**, split by path:

```
local Gemma:      $0.0000  (amortised node cost only)
Bedrock escalated: $0.00XX  (measured tokens × current price)
```

Recording both is what turns "cost-aware model routing" from a design claim into a measured
result. This is the FinOps concept most worth internalising, because it applies far beyond cloud
bills.

### Anomaly detection versus thresholds

Phase 0 used **AWS Budgets** — threshold alerting against a chosen number. Right for a hard ceiling.

**Cost Anomaly Detection** learns normal spend patterns and alerts on statistical deviation. It
catches the case a threshold cannot: spend rising from $11 to $18 is a 64% jump and a real signal,
but no alarm set at $18 fires with any urgency.

Both belong in a mature setup, and they answer different questions: *am I over budget* versus
*is something unusual happening*. The FinOps agent here is essentially doing the second job with
domain knowledge attached — it can say not just "spend changed" but "this node has been idle three
days, and here is the action."

---

## Common mistakes

| Mistake | What it costs |
|---|---|
| Polling Cost Explorer frequently | $0.01 per call adds up, and the data updates daily anyway |
| Expecting tags to apply retroactively | Historical spend is unattributable forever |
| Forgetting to activate cost allocation tags in billing | Tags exist on resources, invisible in cost data |
| Alerting only on actual spend | Lag means you find out late; forecast exists for this |
| Rightsizing on CPU alone | Memory is not reported by default; the recommendation is incomplete |
| Automating rightsizing without a human | The agent sees utilisation, not intent |
| Over-committing to a Savings Plan | You pay for capacity you do not use |
| Optimising total spend rather than unit cost | Efficiency gains are invisible while you grow |
| Leaving a NAT Gateway or ALB running unused | $32 and $18 per month for nothing |

## Glossary

| Term | Meaning |
|---|---|
| **FinOps** | The practice of bringing financial accountability to variable cloud spend |
| **Cost Explorer** | API and console for aggregated cost data; $0.01 per API call |
| **CUR** | Cost and Usage Report — full line-item data delivered to S3 |
| **Athena** | Serverless SQL over S3; the usual way to query a CUR |
| **Granularity** | The time resolution of cost data: hourly, daily, monthly |
| **Cost allocation tag** | A tag activated in billing so it appears in cost data |
| **Unblended cost** | Cost as charged, before organisational discount redistribution |
| **Amortised cost** | Upfront commitment costs spread across the period they cover |
| **Rightsizing** | Matching provisioned capacity to actual need |
| **Savings Plan** | Commitment to hourly spend for 1 or 3 years in exchange for a discount |
| **Reserved Instance** | Older commitment mechanism tied to instance attributes |
| **Spot** | Discounted interruptible capacity; not a commitment |
| **Orphaned resource** | Something still billing with nothing attached to it |
| **Zombie resource** | Something running and billing but serving no traffic |
| **Unit economics** | Cost per unit of value delivered |
| **Showback / chargeback** | Reporting costs to a team / actually billing them |
| **Cost Anomaly Detection** | Statistical alerting on deviation from learned patterns |

## Check yourself

1. When is Cost Explorer the right tool, and when do you need the CUR?
2. You activate a cost allocation tag today. What can you learn about last month's spend?
3. Why is today's reported spend always an underestimate, and what compensates for that?
4. Why is `ManagedBy` arguably the most operationally useful tag?
5. An instance sits at 5% CPU. Give three reasons rightsizing it might still be wrong.
6. Why does CloudWatch not report memory utilisation by default?
7. Why is cost-per-incident a better metric than total monthly spend?
8. Distinguish AWS Budgets from Cost Anomaly Detection, with a case each catches that the other misses.

## In an interview

**"How would you approach cloud cost in a team that has lost control of it?"**

> "Attribution first, because you can't manage what you can't attribute. That means a tagging
> strategy enforced at creation — Terraform default tags, and Config rules or SCPs if people are
> creating things by hand — and accepting that historical spend stays unattributable, because
> tagging isn't retroactive. Then the boring categories: orphaned volumes, idle nodes, zombie NAT
> gateways and load balancers billing for nothing. In my own project I built an agent that
> surfaces those as proposals with the saving attached, and a human approves — deliberately, because
> utilisation metrics show CPU, not intent, and an instance at 5% might be memory-bound or sized
> for a quarterly peak. The metric I'd actually drive is unit cost, not total, because total spend
> grows with success and tells you nothing about whether you're getting more efficient. For my own
> system that's cost per incident processed, split between the self-hosted model and Bedrock, which
> is how I can say the routing saves money rather than just assert it."

Leading with attribution rather than cost-cutting is what marks this as informed.

## Further reading

- FinOps Foundation — *FinOps Framework* (the capabilities model, and where the vocabulary comes from)
- AWS Cost Management User Guide — Cost Explorer, Budgets, Anomaly Detection
- AWS documentation — *Cost and Usage Report* and querying it with Athena
- AWS Well-Architected Framework — the Cost Optimization pillar
- AWS Compute Optimizer — how AWS itself generates rightsizing recommendations
