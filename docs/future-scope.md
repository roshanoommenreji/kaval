# Future scope — beyond v1

Phases 0–9 in [../ROADMAP.md](../ROADMAP.md) are the committed build. This page is the
holding pen for work that is **out of scope for v1** but worth doing afterward, and the
reasoning for why it matters.

The framing question: *does building Kaval make Roshan ready for a Forward Deployed
Engineer role, or the adjacent AI-platform role he is actually aiming at?*

---

## What Kaval already builds

Roughly 70% of the hard-skills bar for both roles:

| Skill | Where Kaval covers it |
|---|---|
| Kubernetes ops, Helm, multi-env promotion | k3s, umbrella chart, `local → staging → prod` with digest pinning |
| Cloud + IaC under real constraints | Terraform modules/envs; the $50/mo ceiling forces the same trade-off reasoning FDEs use daily (Graviton/arm64, no NAT GW, ephemeral EKS) |
| LLM integration | Self-hosted Gemma, Pydantic-validated output, runbook RAG, Bedrock escalation |
| Safe agent design | Read-only `agent` vs. scoped `executor`, human-in-the-loop, evidence-gated autonomy |
| Release engineering | Build-once-promote-artifact, `promote.yml` gate, gitleaks, clean history |
| Written artifacts left behind | ADR / lab / journal discipline as a merge gate |

This is a credible portfolio piece and interview narrative on its own. Stopping after
Phase 5 still yields that.

---

## What a solo greenfield project structurally cannot teach

These are the gaps. None of them are reasons not to build Kaval; they are the reason
Kaval is *necessary but not sufficient*.

1. **No customer.** The core FDE loop is someone else's problem, someone else's
   stakeholders, someone else's definition of done. A solo project can't simulate the
   "walk in with a vague brief" muscle.
2. **No integration hell.** Real FDE work is 50–60% plumbing into systems you don't
   control — legacy APIs, unfamiliar auth, data you can't fully see. Every interface in
   Kaval is one we designed.
3. **Wrong tempo.** Kaval is deliberately slow, gated, and documented. FDE prototyping is
   fast and often disposable, then you harden the 20% that survives. Different reflex.
4. **Narrow stack.** Python + TypeScript only. FDEs often switch stacks per engagement.
5. **Thin frontend, no data engineering, single-tenant, single-node.** No scale,
   multi-tenancy, or performance work anywhere in the plan.

---

## Candidate work to close the gap

Post-v1, not committed. Each is small and deliberately faster / messier than the main build.

### FS-1 — Integration mini-projects (highest value)

Two weekend-sized builds that wire Kaval's agent into a **real third-party system we did
not design**, timeboxed, minimal docs. The point is the plumbing reflex, not the polish.

- Candidate A: ingest a real external billing/metrics API (e.g. a SaaS usage API) as a
  new signal source, mapped into the existing `signal` model.
- Candidate B: push proposals into a real ticketing system (Linear / Jira / PagerDuty)
  instead of only the mobile app, via that system's real API and auth.

Exit: agent acts on data from a system whose API shape we had to reverse-engineer from docs.

### FS-2 — Demo craft

A 5-minute "here is the problem, here is the thing working" walkthrough, recorded and
iterated. FDEs live or die on this. Overlaps with the Phase 8 demo video but is a distinct
skill — practice it as its own thing, against a stopwatch, more than once.

### FS-3 — Stack breadth spike

One small service in a third language (Go for the executor, or a Rust signal parser) to
prove stack mobility. Only if it doesn't compromise the "build once, promote the artifact"
and "environments differ only by values" constraints.

### FS-4 — Client-facing reps

Not code. Use internal "customers" at the current Release Manager job — treat a real team
as a stakeholder, take a fuzzy ask, ship something, demo it back. The only way to build
the dimension items 1–2 above call out.

### FS-5 — Multi-tenant / scale chapter

A dedicated chapter (like the EKS one) that runs Kaval against two simulated clusters with
separate tenancy and a shared control plane. Exercises the parts of platform engineering
Kaval currently skips.

### FS-6 — Petti: a box for the cloud

Jira epic: [KAV-99](https://roshanoommenreji.atlassian.net/browse/KAV-99). Planned 2026-10-28.
**Not started until the current Kaval phase closes.** It would be its own repository.

**The idea.** Kubernetes gave apps a standard box (the container) that runs anywhere. Petti
(Malayalam for "box") would do the same for the cloud: drop any project into a box, give it a
few settings, and have it come up on AWS or another cloud, scaled as asked. Even with AI writing
the code, cloud setup is slow because of *decisions* (which service, size, network, permissions),
*fear* (what will this cost, what will it break) and *day 2* (upgrades, half-failed builds,
leftovers). Petti targets those, not the typing.

**What already exists** (checked 2026-10-28):

| Kind | Tools | Lesson for Petti |
|---|---|---|
| Open "box file" standards | [Score](https://docs.score.dev/docs) (CNCF Sandbox), [Radius](https://opensource.microsoft.com/blog/2025/06/30/expanding-platform-engineering-capabilities-with-radius-resource-types/) recipes, OAM/KubeVela | Score already *is* the box shape: build on it, don't invent one |
| Kubernetes-based bundles | [Crossplane v2](https://blog.crossplane.io/announcing-crossplane-2-0/), [kro](https://pkg.go.dev/github.com/kubernetes-sigs/kro@v0.9.3) | Powerful, but need a cluster: too heavy for "anybody" |
| "Your project → your cloud" products | [Defang](https://github.com/DefangLabs/defang-docs/pull/282/files) (Compose → AWS/GCP), [Encore](https://encore.dev/resources/cloud-infrastructure-automation-tools), Nitric, [HCP Terraform no-code modules](https://developer.hashicorp.com/terraform/cloud-docs/no-code-provisioning/module-design), [ECS Express Mode](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/express-service-overview.html) | Try these first (Stage 0): one may already be enough |
| AI infrastructure agents | [Pulumi Neo](https://www.pulumi.com/docs/ai/neo/), [System Initiative](https://www.businesswire.com/news/home/20250827291941/en) | Propose → human approves is becoming the norm |
| The graveyard | Wing (a cloud *language*, $20M, [shut down 2025](https://thenewstack.io/?p=22784696)); Acorn ([mothballed 2024](https://pipedot.org/6KJNP)); AWS [Proton](https://docs.aws.amazon.com/proton/latest/userguide/proton-end-of-support.html) (retired 2026-10-07), [Copilot CLI](https://aws.amazon.com/blogs/containers/announcing-the-end-of-support-for-the-aws-copilot-cli) (retired 2026-06-12), [App Runner](https://docs.aws.amazon.com/ja_jp/apprunner/latest/dg/apprunner-availability-change.html) (closed to new customers 2026-04-30) | No new language; expect the clouds to move under any box layer |

**The design.** No new language, no Terraform fork, no Kubernetes needed. A small CLI reads a
Score-based box file with a few extra fields and turns it into **ordinary OpenTofu** through
vetted per-cloud recipes:

```yaml
name: my-shop
run:    { build: ./Dockerfile, port: 3000 }
needs:  { database: postgres, files: bucket, domain: shop.example.com }
size: small            # small | medium | large, mapped per cloud
scale: { min: 1, max: 5 }
budget: 20             # $/month; the box refuses to build above it
cloud: aws             # the only line that changes between clouds
secrets: [STRIPE_KEY]  # names only; values live in the cloud's secret store
```

`petti init` (AI **drafts** the box file and never holds cloud credentials, the same split as
Kaval's agent and executor) → `petti plan` (plain-English plan + cost estimate + policy check;
over budget is refused) → human approval → `petti up` (everything tagged) → `petti down` /
`petti eject` (hand back the plain OpenTofu, so no lock-in). "Scale up" means changing one word:
`size: small` might be one cheap server behind Cloudflare Tunnel, while `large` might be ECS
Express Mode behind a load balancer (about $18/mo more for the ALB).

**Stages** (each is an acceptance criterion on KAV-99):

| # | Stage | Gate |
|---|---|---|
| 0 | Try Defang, Score and ECS Express Mode on one sample app | If one fits, use it and close the epic |
| 1 | Box file format + 5 example boxes | Readable by a non-expert |
| 2 | AWS translator: 4 recipes (web service, Postgres, bucket, domain), `plan`/`up`/`down`/`eject` | Up and down with nothing left behind |
| 3 | Safety net: cost estimate, budget refusal, policy, plain-English plan, approval | Over-budget box refused before anything is created |
| 4 | AI drafts the box file | Correct for 5 real projects |
| 5 | GCP recipes | The 5 boxes work unedited except `cloud:` |
| 6 | Web page: boxes, cost, plan, approve | — |

Rough size: an AWS-only version is ~10–12 sessions, and each extra cloud ~4 more.

**Hard problems, stated up front.** The box can only promise what every cloud offers, so it
needs an escape hatch (plain OpenTofu beside it). Moving a box moves the setup, not the data.
Permissions and networking differ per cloud and are where the time really goes. Day 2 work is
most of the job. As a business the space is crowded. As a platform-engineering portfolio piece
it is a strong fit.

**Planning standard, in short.** Tested in a separate throwaway AWS account, never Kaval's.
Every box takes `--env staging|prod`. The AI only drafts files. The tool's role is scoped to its
own tag, and a leftover scan (like `make tag-scan`) catches orphans. Recipes switch on logs and a
health alarm by default. Test boxes are created and destroyed in the same session (cost measured
when built, not guessed). Tests: box → OpenTofu golden files; up → smoke → down; and the negative
case, proving over-budget and dangerous boxes are refused.

---

## Verdict

For a **pure FDE role**: Kaval covers the technical foundation; FS-1, FS-2, and FS-4 are
what actually make the difference, and none of them are in v1.

For the **AI-platform role Roshan is aiming at**: Kaval is better aligned as-is — safe
agent architecture, model deployment, and eval discipline are the platform-team core.
FS-5 is the most relevant add. FS-6 (Petti) is the natural next project after it: it reuses
Kaval's propose-then-approve, policy gate and cost ceiling in a self-service "golden path" tool,
which is what platform teams build.
