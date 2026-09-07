# Budget plan

**Ceiling: $25/month.** Enforced by the system against itself, not by discipline.

Total projected cost for the whole project, Aug 2026 → Mar 2027: **~$47**. Budget **$70** to
absorb one mistake.

---

## Guardrails (Phase 0 — built before any compute exists)

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
| Throughout | Bedrock | — | ~$5 |
| | | **Total** | **~$47** |

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

## Open question

**Does this AWS account still carry free-tier allowance?** AWS restructured the free tier in 2025
and terms differ between older and newer accounts. Verify in Lab 01 rather than assuming — it can
only move the number down.

---

## Actuals

Recorded monthly in [`actuals/`](actuals/). Compare against this plan; when they diverge by more
than 20%, work out why and write it down.
