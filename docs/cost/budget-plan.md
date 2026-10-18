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
| ECR storage, five repos (`KAV-50`, `KAV-61`, `KAV-68`). Measured 2026-10-08: 878 MB across two image sets, about $0.09 at $0.10/GB; each release adds roughly 0.45 GB. Since `KAV-68` ([ADR-0036](../adr/0036-ecr-keeps-the-newest-fifteen-tagged-images.md)) ECR keeps only the newest 15 tagged `sha-*` images per repository, so storage is capped at about 15 × 0.48 GB = 7 GB, **about $0.71/month at the very most** (less in practice, since images in one repository share layers). This row budgets the cap | 0.70 |
| S3 backups | 0.05 |
| CloudWatch logs (trimmed) | 0.50 |
| Bedrock escalations (light use) — Claude Haiku 4.5, cross-region tier, verified 2026-09-30 against the AWS Price List API at $1.00 / $5.00 per 1M input/output tokens ([ADR-0019](../adr/0019-bedrock-escalation-and-the-mantle-client-rejection.md)); occasional escalations at that rate land well under this figure | 1.50 |
| Jev risk rating — TypeSafe API, ~12M input tokens ([ADR-0006](../adr/0006-jev-as-proposal-risk-rater.md)) | 0.50 |
| Data transfer | 1.00 |
| **App node subtotal** (every row above) | **~$26.10** |
| Database server `t4g.small` on-demand, 730 hrs ($0.0112/hr) | 8.18 |
| Database server root disk, 8 GB gp3 | 0.73 |
| Database data volume, 20 GB gp3 ($0.0912/GB-month) | 1.82 |
| Database server public IPv4 ($0.005/hr) | 3.65 |
| Database EBS snapshots, daily, keep 7 (incremental; price to verify in Phase 4) | ~0.30 |
| **Total, worst case** (Bedrock, Jev and transfer at their light-use ceilings; ~$38–39 expected) | **~$40.75** |

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

**Terraform's state bucket (`KAV-86`, [ADR-0058](../adr/0058-terraforms-record-lives-in-a-private-versioned-bucket.md), 2026-10-16) adds under $0.01 a month:** 21 object versions, 0.7 MB measured after the move and the proofs (about $0.00002 of storage at the standard rate), plus a few requests per plan or apply. Encryption uses S3's own free key (a customer key would be $1 a month), the lock is S3's own (no lock table), and the Parameter Store entry is free. No change to the ceiling or the alerts.

**The "still alive" alarm (`KAV-83`, [ADR-0053](../adr/0053-the-still-alive-alarm-lives-in-aws.md), 2026-10-13) adds about $0.72 a month at list price, probably $0:** one CloudWatch custom metric $0.30, the alarm's two metrics $0.20, about 21,600 `PutMetricData` calls a month $0.22 (ap-south-1 prices from the AWS Price List API, 2026-10-13); the group metric and the SNS e-mails are free at this size; CloudWatch's always-free allowance (10 metrics, 10 alarms, 1 million requests) probably covers it all, not verified for this account. Worst-case total above becomes about $41.50. (An always-on watcher server would have been about $7 to $9 a month and was not chosen.)

**The nightly dump itself (`KAV-74`, [ADR-0038](../adr/0038-nightly-dump-is-taken-by-the-app-node.md), 2026-10-10)
adds no spend:** it is a systemd timer on the app node that is already running, writing into the bucket above.
The dump of the current schema is about 20 KB and 14 days are kept, so storage stays within the S3 line (0.05).
No new resource, no change to the ceiling or the alerts. Staging gets the same timer, writing into staging's own
bucket; a staging dump taken by hand during a drill costs the same fraction of a cent.

One-off, the same day: switching prod on to take the first real dump (the app node plus the database server for
about an hour, and a failed first attempt that left the database server running a little longer) cost roughly $0.10.
The measured dump was 22 KB.

Staging seeding (`KAV-73`, [ADR-0039](../adr/0039-staging-is-filled-from-a-copy-cleaned-on-the-laptop.md), 2026-10-10) adds no
recurring spend and no resource: the cleaned file is about 20 KB in staging's own bucket, which is destroyed with staging
(`force_destroy`), and the cleaning runs in Docker on the laptop. The live drill is one ordinary staging session, about
$0.045/hour for one to two hours, so roughly 10 cents (the "$1-2" first estimated was an upper bound, not the expected cost).
Measured, 2026-10-10: about 25 minutes of staging, a few cents; the seed file and staging's bucket were destroyed with it.

**The first run of production on the finished Phase 5 (`KAV-83` Part 3, 2026-10-14)** added the "still alive" alarm and
its topic (about $0.72 a month at most, already in the table above) and cost one ordinary session: production's two
servers for about an hour and a quarter (the app server $0.0224 plus the database server $0.0112 plus two public
addresses at $0.005, about $0.045 an hour together) and staging for about 20 minutes: **roughly 7 cents**. Two
snapshots of the 20 GB data volume add a few cents a month while they are kept.

**Phase 6 (`KAV-91`, [ADR-0059](../adr/0059-always-on-cost-model-and-measuring-usage-before-credits.md), 2026-10-16) redoes this table for always-on and finds it holds.** Expected about $39 a month, worst case about $42.10: the $41.50 above plus the cost reader, whose Cost Explorer calls ($0.01 each, [AWS's pricing page](https://aws.amazon.com/aws-cost-management/aws-cost-explorer/pricing/), read 2026-10-16) are capped at 60 a month, $0.60 at most, about 35 expected. Waste scans use free read calls. The ceiling and the alerts are unchanged; the first alert ($42) now sits at the top of the worst case, which would be information, not noise.

**The waste scan (`KAV-94`, [ADR-0062](../adr/0062-waste-is-found-daily-by-a-read-only-scan-and-reported-to-a-person.md), 2026-10-18)** adds **$0 a month**: one Lambda run a day (30 a month, free tier), four free Describe calls, one standard parameter (free), one more ConfigMap read by the cost reader's existing pod (no new pod). Proving it on staging plants a 1 GiB disk for a day or two, about $0.09 a month while it exists. No ceiling or alert change.

**Tags and the show-back report (`KAV-93`, [ADR-0061](../adr/0061-every-resource-carries-three-tags-and-the-bill-is-split-by-them.md), 2026-10-18)** add no recurring AWS spend: activating `Env` and `ManagedBy` as billing labels and the 12-month backfill are free, and each `make cost-showback` is one Cost Explorer request, $0.01 (about $0.12 a year if run monthly). No server memory or processor change. AWS Config rules and organisation-wide tag policies were rejected partly on cost (Config bills per recorded item and per evaluation).

**The cost reader (`KAV-92`, [ADR-0060](../adr/0060-the-cost-reader-the-server-asks-aws-and-the-cluster-never-holds-the-login.md), 2026-10-18)** asks Cost Explorer once a day for the last 14 days: about 31 requests a month, **$0.31**, hard-capped at 60 ($0.60) inside the server's job. The cap counts on the server, so a rebuilt server counts from zero again (cents; this budget is the backstop). Its own requests show in the bill as "AWS Cost Explorer" (10 cents on 2026-10-16, from the tests that built it). No new server and no new managed service; one small pod limited to 128 MB. The worst-case total above already carries it.

**The credits do not stretch over a long always-on run (checked 2026-10-16).** About $132 of the $140 is left (usage so far: September $1.39, October to the 16th $6.41). At about $39 a month that is roughly three and a half months of always-on, to about the end of January 2027, then the card is charged. The projection table below assumed two always-on months (Feb–Mar 2027), written before Phase 6 started now. The rule from here: stay always-on only while a phase needs it, park between phases, and read the credit balance in the console at the start of each phase.

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

Measured, 2026-10-14 (`KAV-84`, Lab 51): about two to two and a half hours of the dev server running five full
before-and-after eval passes (about 200 diagnoses) and the probes in between cost about 6 cents of server and address time. The surplus-CPU line
above is the part not yet read off the bill; the model runs the CPU flat out, so up to about 8 cents an hour is possible.

**The model in the cluster (KAV-87, [ADR-0055](../adr/0055-the-model-runs-in-the-cluster-and-diagnoses-new-incidents.md)).**
Recurring AWS cost: **$0**. It runs on the app node and uses its existing root disk (about 1.1 GB of the 20 GB); no
new machine, volume or address. The only variable cost is surplus CPU: a diagnosis uses up to 1.5 vCPUs for 10 to 60
seconds, the speed limit is 6 an hour, so the worst case (one workload flapping every hour all month) is about 109
vCPU-hours, **at most about $4.40 a month**, and only if the node's earned credits ran out. Memory is the real
cost: about 1.13 GB while loaded, unloaded after 5 idle minutes. **Measured on staging 2026-10-15:** the node earned credits
the whole time (balance 0 to 29.5 in 2 h 15 min) and was charged **$0** for surplus CPU; at 6 diagnoses an hour the
worst case is under 10 credits an hour against 24 earned, so the $4.40 above cannot happen at these settings. The line
to watch on the bill is still surplus credits, which stays at $0 while the balance is positive.
**Measured on production 2026-10-16:** still **$0** recurring; 872 MB of memory stayed free at the worst moment (2,007
MB idle), and in the first 50 minutes after the boot the surplus-credit charge was $0 (a fresh node starts with no
credits, as staging did). Production costs about $0.044 an hour while up, unchanged.

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

First real use (`KAV-62`, Lab 33, 2026-10-08): one build, the smoke test and a deliberate-fail run, then
`make staging-down`, under an hour, so about $0.05 at that rate.

Rollback rehearsal (`KAV-66`, Lab 35, 2026-10-09): staging up for 1 h 45 min (a failed rollback, a database
undo, a successful rollback), then destroyed: about $0.08.

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
| GitHub repo (public since 2026-09-30) | $0 |
| GitHub Actions CI (`KAV-24`) and `release.yml` (`KAV-61`) — GitHub-hosted runners, including the arm64 ones, are free for a public repo. It was measured when the repo was private (a run billed 6 minutes of a 2,000 minute allowance); that cap no longer applies, and the figure is kept as a record of the cost of a run | $0 |
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

**Re-checked 2026-10-16 ([ADR-0059](../adr/0059-always-on-cost-model-and-measuring-usage-before-credits.md)):** credits applied so far equal usage to the cent (September $1.3896, October to the 16th $6.4119), about $132 remain, and the live budget `kaval-monthly` counts credits (`IncludeCredit = true`), so it reads $0.00 of $50 and its alerts and hard stop would not fire until the credits are nearly gone. **Changed the same day on Roshan's go:** the budget now leaves credits and refunds out and reads $6.412 of $50, so its alerts and hard stop mean real usage.

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

`make cost-report` prints gross usage, credits and refunds left out (changed 2026-10-16, `KAV-91`: it used to print the net figure, which the account's credits turn into $0). Cost Explorer lags about a day, so write the record on the 2nd or later for final numbers.
