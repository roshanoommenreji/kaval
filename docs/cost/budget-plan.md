# Budget plan

**Ceiling: $25/month.** Enforced by the system against itself, not by discipline.

Total projected cost for the whole project, Aug 2026 → Mar 2027: **~$52**. Budget **$70** to
absorb one mistake.

---

## Guardrails (Phase 0 — built before any compute exists)

**Status: applied and proven, 2026-09-15.** `terraform apply` created 10 resources (SNS topic +
policy, 2 subscriptions, the budget, IAM role + policy, the Lambda, its permission, and a log
group) — nothing billable. The Lambda was invoked manually; the CloudWatch log confirms it ran
with `dry_run=True` and the correct "nothing to scale" warning, since no compute exists yet.

| Trigger | Action |
|---|---|
| MTD ≥ $18 | Email alert |
| MTD ≥ $22 | Second email alert |
| MTD ≥ $24 | Lambda scales the ASG to zero — the cluster stops |

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
| 5 — Mobile | 16–19 | **Paused** | Push alerts demo fine within a session |
| 6 — Chaos | 20–21 | **Paused** | Chaos runs are deliberate, not ambient |
| 7 — FinOps | 22–24 | **Always-on** | Cost Explorer is daily-granularity with ~24 h lag; an idle account has no waste to find |
| 8–9 — EKS, publish | 25–28 | **Always-on** | "Running continuously since February" is the credibility claim |

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
| `t4g.medium` spot, 730 hrs | 9.20 |
| EBS gp3, 20 GB | 1.60 |
| ECR storage | 0.20 |
| S3 backups | 0.05 |
| CloudWatch logs (trimmed) | 0.50 |
| Bedrock escalations (light use) | 1.50 |
| Jev risk rating — TypeSafe API, ~12M input tokens ([ADR-0006](../adr/0006-jev-as-proposal-risk-rater.md)) | 0.50 |
| Data transfer | 1.00 |
| **Total** | **~$14** |

Paused, the same account costs **~$2.20/month** — storage only.

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
| | | **Total** | **~$67** |

### The dev server (ADR-0007)

Prices verified 2026-09-26 against the AWS Price List API for ap-south-1.

| Item | Rate | At ~20 h/week (~87 h/mo) |
|---|---|---|
| `t4g.medium` on-demand | $0.0224/hr, only while running | $1.95 |
| Public IPv4 address | $0.005/hr, only while running | $0.44 |
| 30 GB gp3 disk | $0.0912/GB-month, **also while stopped** | $2.74 |
| **Per month** | | **~$5.10** |

Left running around the clock by mistake, the compute alone would be $16.35/month. The idle stop
exists to make that mistake impossible. The **$24 hard-stop Lambda does not cover this server**:
it targets the Phase 4 auto-scaling group, so the protection here is the idle stop plus the
$18/$22 alerts.

About 3 months of Phases 1–3 at ~$5/month adds ~$15, so the projected total rises from ~$52 to
~$67. That's still inside the $70 budget-with-buffer, but the buffer shrinks from $18 to $3,
which is thin. `make devbox-down` at the end of each session is the easiest saving. All of it
comes out of the $140 of AWS credit (expires 2027-09-11), so the expected out-of-pocket cost is still close to
zero.

### The staging cluster

A genuine second cluster — its own `t4g.medium` spot node, its own k3s, own etcd, own database,
own VPC. Exact parity with prod, because memory pressure on 4 GB is this project's binding
constraint and a smaller staging node would miss precisely that.

It exists **on demand**: `make staging-up` builds it in ~5 minutes, and it self-destructs after
four idle hours.

| | |
|---|---|
| `t4g.medium` spot, ~8 hrs/month | $0.10 |
| EBS gp3 10 GB (persisted between releases) | $0.80 |
| **Per month** | **~$1** |

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
| Application Load Balancer | $18/mo | Cloudflare Tunnel (free, HTTPS, no inbound ports) |
| EKS control plane, persistent | $73/mo | k3s; EKS only as an ephemeral chapter |
| RDS | $12+/mo | Postgres in-cluster on an EBS PV, nightly dump to S3 |
| GPU instance | $0.30+/hr | CPU inference; Bedrock for anything heavy |
| On-demand EC2 | $24/mo | Spot at ~$9/mo |

Conventional equivalent of this stack: **~$153/month**. This one: **~$14**.

---

## Non-AWS costs

| Item | Cost |
|---|---|
| Jira Cloud (free tier, ≤10 users) | $0 |
| GitHub private repo | $0 |
| Cloudflare Tunnel | $0 |
| Expo local builds | $0 |
| Gemma weights | $0 |
| TypeSafe Jev API — waitlist, no free tier, billed on input only | ~$0.50/mo from Phase 2 — **outside the AWS credit and outside the Lambda hard stop**; set a spend cap in the TypeSafe console |
| Domain for a stable tunnel hostname | ~$12/yr — **optional** |
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
**entire ~$52 projected spend above is very likely absorbed by this credit alone** — the $25/mo
ceiling and its Lambda hard-stop stay in force regardless, as an independent guardrail, but the
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
The credit outlives the project's March 2027 target by six months, and the ~$67 projection
(including the dev server, ADR-0007) leaves about $73 unused. **Expected out-of-pocket cost:
still $0.**

The Free Plan limits EC2 to free-tier sizes, so `t4g.medium` was refused (`InvalidParameterCombination: not eligible for Free Tier`).
Running it needs the account on the **paid plan**. That upgrade has no fee, keeps these credits,
and cannot be undone. After it, spend beyond the credit bills the card instead of stopping the
account, which makes this project's own guardrails (alerts at $18/$22, hard stop at $24, the dev
server's idle stop) the only brake.

Re-check this figure periodically — a credit-based plan can behave differently from classic free
tier at the edges, and the number should be verified against the console rather than assumed to
still read $100 by the time Phase 7 goes always-on.

---

## Actuals

Recorded monthly in [`actuals/`](actuals/). Compare against this plan; when they diverge by more
than 20%, work out why and write it down.
