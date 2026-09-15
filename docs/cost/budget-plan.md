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
| 0–3 | 1–12 | **Laptop only** | Nothing on AWS but a budget alarm |
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
| Data transfer | 1.00 |
| **Total** | **~$14** |

Paused, the same account costs **~$2.20/month** — storage only.

---

## Projected total

| Period | Phases | Posture | Cost |
|---|---|---|---|
| Aug–Oct 2026 | 0–3 | Laptop | ~$0 |
| Nov 2026–Jan 2027 | 4–6 | Paused | ~$5 |
| Feb–Mar 2027 | 7–9 | Always-on | ~$22 |
| Occasional | EKS lab × 3 | Ephemeral | ~$15 |
| **Per release** | **staging cluster** | **On demand** | **~$5** |
| Throughout | Bedrock | — | ~$5 |
| | | **Total** | **~$52** |

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

Re-check this figure periodically — a credit-based plan can behave differently from classic free
tier at the edges, and the number should be verified against the console rather than assumed to
still read $100 by the time Phase 7 goes always-on.

---

## Actuals

Recorded monthly in [`actuals/`](actuals/). Compare against this plan; when they diverge by more
than 20%, work out why and write it down.
