# Budget plan

**Ceiling: $50/month.** Enforced by the system against itself, not by discipline. Raised from
$25 to $40 on 2026-09-26, when the production database moved to its own server
([ADR-0008](../adr/0008-production-database-on-its-own-server.md)), and from $40 to $50 on
2026-10-08, when the app node went On-Demand ([ADR-0028](../adr/0028-prod-app-node-on-demand-and-ceiling-50.md)).

Total projected cost for the whole project, Aug 2026 → Mar 2027: **~$126**. The budget is the
**$140 AWS credit** (expires 2027-09-11), which leaves ~$14 to absorb a mistake.

---

## Guardrails (Phase 0 — built before any compute exists)

**Status: applied and proven, 2026-09-15.** `terraform apply` created 10 resources (SNS topic +
policy, 2 subscriptions, the budget, IAM role + policy, the Lambda, its permission, and a log
group) — nothing billable. The Lambda was invoked manually; the CloudWatch log confirms it ran
with `dry_run=True` and the correct "nothing to scale" warning, since no compute exists yet.

| Trigger | Action |
|---|---|
| MTD ≥ $42 | Email alert |
| MTD ≥ $46 | Second email alert |
| MTD ≥ $48 | Lambda scales the ASG to zero (from Phase 4; dry-run until then) **and stops every running `Project=kaval` server outside an ASG**, e.g. the dev server and, from Phase 4, the database server. Armed and fired for real 2026-09-26 (`KAV-30`) |
| 02:00 IST, nightly | The same Lambda, triggered by an EventBridge Scheduler rule instead of a spend threshold — a brake for a forgotten `make down`, not a second cost. Harmless when nothing was forgotten: both triggers already no-op on an ASG at `desired=0` and skip stopped instances (`KAV-32`, Lab 25). Switches off from Phase 7 (`nightly_auto_stop_enabled`) |
| Forecast ≥ $50 | Email alert that the month is heading over the ceiling |

Thresholds were $18 / $22 / $24 / $25 until 2026-09-26, then $30 / $35 / $38 / $40 (ADR-0008),
and since 2026-10-08 are $42 / $46 / $48 / $50 (ADR-0028). The first alert sits above the
always-on steady state (~$38–39 expected, ~$40 worst case) rather than at it, because an alert
that fires on normal spend trains you to ignore it. The hard stop sits ~$8 above the worst case.

This ordering is deliberate. The thing that stops the bill was provisioned and tested *before* the
first thing that could create one.

---

## Run posture

Always-on is not needed for the whole project. Paying for it from day one wastes about $35.

| Phase | Weeks | Posture | Why |
|---|---|---|---|
| 0 | 1–2 | **Laptop only** | Nothing on AWS but a budget alarm |
| 1–3 | 3–12 | **Dev server, stops when idle** | Changed 2026-09-26 by [ADR-0007](../adr/0007-develop-on-an-aws-dev-server.md): the stack runs on a `t4g.medium` in AWS instead of the laptop, for real arm64 measurements. It stops itself after 1 idle hour |
| 4 — AWS landing | 13–15 | **Paused** | You are at the keyboard anyway |
| 5 — Chaos | 16–17 | **Paused** | Chaos runs are deliberate, not ambient |
| 6 — FinOps | 18–20 | **Always-on** | Cost Explorer is daily-granularity with ~24 h lag; an idle account has no waste to find |
| 7–8 — EKS, publish | 21–24 | **Always-on** | "Running continuously since February" is the credibility claim |
| 9 — Mobile | 25–28 | **Deferred** | Not yet scheduled ([ADR-0026](../adr/0026-slack-chatops-and-deferred-mobile.md)) — Slack ChatOps (Phase 4) already provides approval |

```bash
make up      # ~5 min — Flux reconciles everything from Git
make down    # node destroyed; EBS + ECR + S3 persist at ~$2.20/mo
```

`make down` is also the honest failure mode. If money gets tight the project parks at ~$2/month
indefinitely and resumes without loss.

---

## Steady-state cost, always-on

| Item | $/mo |
|---|---|
| `t4g.medium` **On-Demand**, 730 hrs at $0.0224/hr ([ADR-0028](../adr/0028-prod-app-node-on-demand-and-ceiling-50.md), 2026-10-08). Was Spot at $0.0103–0.0109/hr (~$7.80/mo, verified live 2026-10-02, `KAV-50`) until Spot capacity ran out in every AZ on 2026-10-06 and 2026-10-08; `spot = true` brings the saving back | 16.35 |
| EBS gp3, 20 GB | 1.82 |
| Public IPv4 ($0.005/hr) | 3.65 |
| ECR storage, four repos (`KAV-50`) | 0.20 |
| S3 backups | 0.05 |
| CloudWatch logs (trimmed) | 0.50 |
| Bedrock escalations (light use) — Claude Haiku 4.5, cross-region tier, verified 2026-09-30 against the AWS Price List API at $1.00 / $5.00 per 1M input/output tokens ([ADR-0019](../adr/0019-bedrock-escalation-and-the-mantle-client-rejection.md)); occasional escalations at that rate land well under this figure | 1.50 |
| Jev risk rating — TypeSafe API, ~12M input tokens ([ADR-0006](../adr/0006-jev-as-proposal-risk-rater.md)) | 0.50 |
| Data transfer | 1.00 |
| **App node subtotal** (every row above) | **~$25.60** |
| Database server `t4g.small` on-demand, 730 hrs ($0.0112/hr) | 8.18 |
| Database server root disk, 8 GB gp3 | 0.73 |
| Database data volume, 20 GB gp3 ($0.0912/GB-month) | 1.82 |
| Database server public IPv4 ($0.005/hr) | 3.65 |
| Database EBS snapshots, daily, keep 7 (incremental; price to verify in Phase 4) | ~0.30 |
| **Total, worst case** (Bedrock, Jev and transfer at their light-use ceilings; ~$38–39 expected) | **~$40.25** |

**Corrected 2026-10-08.** This table used to read "app node subtotal ~$13" and "total ~$27". Those
figures counted only compute, disk and IPv4 for the app node: the ECR, S3, logs, Bedrock, Jev and
transfer rows (about $3.75) were listed but never added in. The true always-on figure on Spot was
~$31.70; On-Demand adds $8.55 to it.

The database server ([ADR-0008](../adr/0008-production-database-on-its-own-server.md)) is
**on-demand, never spot**, because a database must not be reclaimable at two minutes' notice.
Prices are from the AWS Price List API for ap-south-1, 2026-09-26.

**Built and applied 2026-10-04 (`KAV-32`, `infra/modules/database`)** — these figures are now
actual, not projected. One addition not in the table above: an S3 bucket for the nightly
`pg_dump` (`infra/modules/backups`), a few cents a month at this data volume — negligible next
to the figures already here, and the free S3 gateway VPC endpoint means it never crosses a
metered data-transfer path.

Paused, the same account costs **~$4.80/month**: storage only, the app node's ~$2.20 plus the
database server's two disks (~$2.55).

---

## Projected total

| Period | Phases | Posture | Cost |
|---|---|---|---|
| Aug–Sep 2026 | 0 | Laptop | ~$0 |
| **Oct–Dec 2026** | **1–3** | **Dev server, idle stop (ADR-0007)** | **~$15** |
| Nov 2026–Jan 2027 | 4–6 | Paused | ~$5 |
| Feb–Mar 2027 | 7–9 | Always-on | ~$22 |
| Occasional | EKS lab × 3 | Ephemeral | ~$15 |
| **Per release** | **staging cluster** | **On demand** | **~$5** |
| Throughout | Bedrock | — | ~$5 |
| Nov 2026–Jan 2027 | 4–6 | **Database server, paused with prod (ADR-0008)** | **~$12** |
| Feb–Mar 2027 | 7–9 | **Database server, always-on** | **~$29** |
| Per release | staging database server | On demand | ~$1 |
| Feb–Mar 2027, plus a little in paused months | 4–9 | **App node On-Demand instead of Spot (ADR-0028)** | **~$16** |
| | | **Total** | **~$126** |

The On-Demand row was added on 2026-10-08: ~$8.55/month over ~1.6 always-on months (Phases 6–8)
plus ~$2 across the paused months. The database rows were added on 2026-09-26. The plan that proposed them estimated ~$100–105; the
itemised sum is ~$110, and ~$110 is the figure used everywhere.

### The dev server (ADR-0007)

Prices verified 2026-09-26 against the AWS Price List API for ap-south-1.

| Item | Rate | At ~20 h/week (~87 h/mo) |
|---|---|---|
| `t4g.medium` on-demand | $0.0224/hr, only while running | $1.95 |
| Public IPv4 address | $0.005/hr, only while running | $0.44 |
| 30 GB gp3 disk | $0.0912/GB-month, **also while stopped** | $2.74 |
| CPU above the 20% baseline (T4g "unlimited" credit mode, the default) | $0.04 per vCPU-hour of surplus credits | ~$0.05 measured; $1.30 worst case |
| **Per month** | | **~$5.10** |

**CPU credits, verified 2026-09-27 (KAV-23).** A `t4g.medium` earns CPU credits for 20% of its
2 vCPUs. In *unlimited* mode, going above that isn't throttled; the extra is billed at $0.04 per
vCPU-hour. The balance starts near zero after every start, so image builds and model runs early in
a session draw surplus credits. Measured: 7.4 surplus credits (about $0.005) in the KAV-22 bench
session, and about the same on 2026-09-27. Worst case, 100% of both vCPUs for a whole hour, is
about $0.064/hr on top of the $0.0224/hr instance. *Standard* mode would remove the charge, but it
throttles to 20% CPU, which makes local-model inference unusably slow. So unlimited stays, and this
row makes the cost visible.

Left running around the clock by mistake, the compute alone would be $16.35/month. The idle stop
exists to make that mistake impossible. Behind it, three layers:

1. The **idle stop** on the server itself: 60 minutes with nobody connected.
2. **Email alerts** at $42 and $46 (were $18 and $22 before ADR-0008, $30 and $35 before ADR-0028).
3. The **$48 hard-stop Lambda** (was $24, then $38), which stops the server automatically (`KAV-30`).

The Lambda reacts within hours rather than minutes, because AWS Budgets data lags.

About 3 months of Phases 1–3 at ~$5/month adds ~$15, so the projected total rises from ~$52 to
~$67. (That was inside the $70 budget-with-buffer of the time. ADR-0008 later took the
projection to ~$110 against the $140 credit.) `make devbox-down` at the end of each session is the easiest saving. All of it
comes out of the $140 of AWS credit (expires 2027-09-11), so the expected out-of-pocket cost is still close to
zero.

### The staging cluster

A genuine second cluster — its own `t4g.medium` On-Demand node, its own k3s, own etcd, own database,
own VPC. Exact parity with prod, because memory pressure on 4 GB is this project's binding
constraint and a smaller staging node would miss precisely that.

It exists **on demand**: `make staging-up` builds it in ~5 minutes, and it **parks itself** after
four idle hours (a Lambda scales the node to zero and stops the database server;
[ADR-0029](../adr/0029-staging-parks-itself-when-idle.md)). `make staging-down` destroys it.

| | |
|---|---|
| `t4g.medium` On-Demand, ~8 hrs/month ($0.0224/hr) | $0.18 |
| Database server `t4g.small`, ~8 hrs/month ($0.0112/hr) | $0.09 |
| Two public IPv4 addresses, ~8 hrs/month | $0.08 |
| Disks while up (nothing persists: the data volume is deleted at teardown) | ~$0.05 |
| **Per month** | **~$0.50** |

Measured 2026-10-08 (`KAV-57`, Lab 29): about $0.045/hour while up, and 48 resources apply in 71
seconds. Staging is On-Demand because the Spot shortage that stalled Lab 28 hit it too.

**Parked is not free.** After the idle stop the node is gone, but the database server keeps its 8 GB
root disk and 10 GB data volume: 18 GB × $0.0912/GB-month (gp3, Mumbai, Price List API, 2026-10-08) =
**~$1.64/month**, plus a few cents of pre-stop snapshot. That is the cost of parking over destroying
(ADR-0029), and `make staging-down` ends it. Lambda and EventBridge Scheduler for the idle check are
inside the free tier (about 2,900 invocations a month).

Always-on it would be ~$11/month and idle roughly 95% of the time — which would take the project
total to ~$102 and require raising the ceiling. See
[ADR-0004](../adr/0004-environment-strategy-and-promotion.md).

After v1: ~$14/month to keep the demo live. Worth paying during an active job search;
`make down` otherwise.

---

## Architecture choices made for cost

These are not accidents. Each avoided line item exceeds the entire monthly budget or a large
fraction of it.

| Avoided | Would cost | Used instead |
|---|---|---|
| NAT Gateway | $32/mo | Public subnet + security groups |
| Application Load Balancer | $18/mo | Slack ChatOps over an outbound Socket Mode connection (`KAV-55`) — no inbound endpoint at all. A Cloudflare Tunnel was the original plan but is deferred with mobile (ADR-0026); it would avoid the same $18/mo if ever needed |
| EKS control plane, persistent | $73/mo | k3s; EKS only as an ephemeral chapter |
| RDS | ~$18/mo Single-AZ, ~$36/mo Multi-AZ | Self-managed Postgres on its own `t4g.small` EC2 server (~$14/mo), snapshots + nightly dump to S3 ([ADR-0008](../adr/0008-production-database-on-its-own-server.md)). Until 2026-09-26 the plan was Postgres inside the app node |
| GPU instance | $0.30+/hr | CPU inference; Bedrock for anything heavy |
| On-demand EC2 | $24/mo | Spot at ~$9/mo, until Spot capacity ran out in every AZ twice; the app node has been On-Demand since 2026-10-08 (~$16/mo, [ADR-0028](../adr/0028-prod-app-node-on-demand-and-ceiling-50.md)) |

Conventional equivalent of this stack: **~$153/month**. This one: **~$40 worst case, ~$38–39
expected** always-on (corrected 2026-10-08: it read ~$28, which left out about $3.75 of listed
items and, since ADR-0028, $8.55 of On-Demand). Most months are paused and cost far less.

---

## Non-AWS costs

| Item | Cost |
|---|---|
| Jira Cloud (free tier, ≤10 users) | $0 |
| GitHub private repo | $0 |
| GitHub Actions CI (`KAV-24`) — Free plan's 2,000 min/month for private repos; a run bills 6 (68 s, each job rounded up to a minute), ~12 per PR | $0 — if the minutes ran out, runs stop; nothing is billed without a payment method and a spending limit above $0 |
| Slack (ChatOps, free tier) | $0 |
| Expo local builds | $0 — **not yet needed**, mobile deferred (ADR-0026) |
| Gemma weights | $0 |
| TypeSafe Jev API — waitlist, no free tier, billed on input only | ~$0.50/mo from Phase 2 — **outside the AWS credit and outside the Lambda hard stop**; set a spend cap in the TypeSafe console |
| Cloudflare Tunnel + domain for a stable hostname | ~$12/yr — **not currently needed**; Slack ChatOps uses no public endpoint. Revisit only if mobile is built (Phase 9) |
| Google Play publishing | $25 one-time — **not needed**, sideload the APK |
| Apple Developer | $99/yr — **not applicable**, Android only |

---

## The four ways this goes wrong

Every AWS horror story is one of these. All four are avoided by design or caught by the guardrails.

| Mistake | Cost |
|---|---|
| NAT Gateway left running a month | $32 |
| GPU instance forgotten over a weekend | ~$50 |
| EKS cluster not destroyed after a lab | $73/mo |
| Load balancer orphaned after teardown | $18/mo |

After every `make lab-down`, run `make cost-report` the following day. A portfolio project that
quietly bills forever is an anti-credential.

---

## Free plan (checked in Lab 01, 2026-09-11)

This account is on AWS's newer **Free Plan**, not the classic per-service free tier:

| | |
|---|---|
| Credit balance | **$100.00** |
| Days remaining | **182** (expires ≈ 2027-03-12) |
| Charging model | Usage draws down the credit balance; nothing bills a card until the credit is spent or the plan expires |

That expiry lines up almost exactly with the project's target finish. In practice this means the
**entire ~$52 projected spend (as it stood then) is very likely absorbed by this credit alone** — the monthly
ceiling (then $25, then $40, now $50) and its Lambda hard-stop stay in force regardless, as an independent guardrail, but the
realistic out-of-pocket exposure for the whole project is close to $0 rather than $52.

### Corrected 2026-09-26, from Billing → Credits

The figures above were partly wrong. **182 days was the length of the Free *Plan*, not the
credits' life.** The Credits page shows:

| Credit | Amount | Expires |
|---|---|---|
| AWS Free Tier | $100.00 | 2027-09-11 |
| Explore AWS: create a web app using AWS Lambda | $20.00 | 2027-09-11 |
| Explore AWS: set up a cost budget using AWS Budgets | $20.00 | 2027-09-11 |
| **Total remaining** | **$140.00** ($0.00 used) | |

The two $20 credits were earned by Phase 0's own work: the hard-stop Lambda and the budget.
The credit outlives the project's March 2027 target by six months. The ~$67 projection
(including the dev server, ADR-0007) left about $73 unused; with the database server
(ADR-0008) the projection was ~$110, leaving about $30; with the On-Demand app node
(ADR-0028) it is ~$126, leaving about $14. **Expected out-of-pocket cost: still $0**, but the margin
for a mistake is now thin.

The Free Plan limits EC2 to free-tier sizes, so `t4g.medium` was refused (`InvalidParameterCombination: not eligible for Free Tier`).
Running it needs the account on the **paid plan**. That upgrade has no fee, keeps these credits,
and cannot be undone. After it, spend beyond the credit bills the card instead of stopping the
account, which makes this project's own guardrails (alerts at $42/$46, hard stop at $48, the dev
server's idle stop) the only brake.

Re-check this figure periodically — a credit-based plan can behave differently from classic free
tier at the edges, and the number should be verified against the console rather than assumed to
still read $100 by the time Phase 6 goes always-on.

---

## Actuals

Recorded monthly in [`actuals/`](actuals/), one file per finished month: `actuals/YYYY-MM.md`.
Compare against this plan; when they diverge by more than 20%, work out why and write it down.

Nothing has to remember this (`KAV-19`). From the 1st of each month, the dashboard lists the
previous month under **Blocked on** until its file exists. It checks every month from
`actuals_from` in `scripts/tracking/dashboard.toml` (2026-09, the first month with AWS usage).

A record holds four things:
- the month's gross usage by service, before credits: Cost Explorer with `RECORD_TYPE` Credit
  and Refund excluded;
- the credits applied;
- the amount actually billed;
- one line comparing it with this plan, and why if they differ by more than 20%.

`make cost-report` prints the net figure. Cost Explorer lags about a day, so write the record on
the 2nd or later for final numbers.
