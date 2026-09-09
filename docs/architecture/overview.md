# Architecture overview

> **Live diagrams:** [system topology](https://claude.ai/code/artifact/c6ca9411-2b4b-47a8-81c1-3f3cc4c34d0c#system) · [incident journey](https://claude.ai/code/artifact/c6ca9411-2b4b-47a8-81c1-3f3cc4c34d0c#journey)
> Generated from [`architecture.toml`](../../architecture.toml), with each component's state
> derived from `ROADMAP.md` — so they show what exists today, not just the target.
> Tap any component for its inputs, outputs and credentials.

## The loop

```
┌──────────┐   ┌──────────┐   ┌─────────┐   ┌──────────┐   ┌────────┐
│ collector│──▶│  agent   │──▶│ policy  │──▶│ gateway  │──▶│ phone  │
│          │   │          │   │         │   │          │◀──│approve │
│ k8s      │   │ correlate│   │ auto    │   │ push     │   └────────┘
│ prom     │   │ context  │   │ ask     │   │ ws/rest  │        │
│ costexpl │   │ LLM      │   │ never   │   └──────────┘        │
└──────────┘   └──────────┘   └─────────┘                       ▼
     │              │                                     ┌──────────┐
     │              │  READ-ONLY                          │ executor │
     ▼              ▼                                     │  WRITE   │
┌──────────────────────────────────────────────────┐      └──────────┘
│  postgres + pgvector                             │            │
│  signal · incident · proposal · action           │◀───────────┘
│  decision · execution · outcome                  │  records what happened
└──────────────────────────────────────────────────┘
                     │
                     └──▶ outcome feeds the next context (and the autonomy case)
```

## Services

| Service | Language | Responsibility | Credentials |
|---|---|---|---|
| `collector` | Python | Scrape Prometheus, K8s events, Alertmanager webhooks, Cost Explorer. Normalise into `signals`. | read-only |
| `agent` | Python | Correlate signals into incidents. Build context. Call the LLM. Emit a validated `Proposal`. | **read-only** |
| `executor` | Python | The only component that mutates anything. Consumes approved proposals. Records before/after state. | scoped write |
| `gateway` | Python / FastAPI | Mobile-facing REST + WebSocket. Auth. Push dispatch. | own DB only |
| `inference` | Ollama | Serves Gemma 3 1B (q4) over an OpenAI-compatible API. | none |
| `chaos` | K8s CronJob | Injects controlled failures into a labelled namespace. | scoped write |

## The privilege split

This is the load-bearing design decision and the first thing an interviewer should probe.

The `agent` reasons but cannot act. Its sole output is a row in `proposals`. It holds read-only
Kubernetes RBAC and a read-only AWS role. Even a fully compromised or hallucinating model can
only write a suggestion into a table.

The `executor` acts but cannot reason. It reads approved proposals, checks each action against
policy a second time, executes, and records the outcome. It never calls an LLM.

Between them sits the policy engine and, for anything not yet promoted to `auto`, a human.

Consequence: prompt injection through log lines or Kubernetes event text — a real attack, since
the agent reads attacker-influenceable data — cannot escalate into cluster access. The worst case
is a bad proposal, which the policy `never` class and the human both still have to pass.

## Data model

Append-only. Nothing is ever updated in place, so the table *is* the audit trail.

| Table | Holds |
|---|---|
| `signal` | One raw observation: `{source, kind, target, value, ts}` |
| `incident` | A correlated group of signals: `{severity, fingerprint, opened_at, closed_at}` |
| `proposal` | LLM output: `{summary, root_cause, confidence, risk, model, tokens, cost}` |
| `action` | One concrete operation: `{type, target, params, reversible, blast_radius}` |
| `decision` | Human verdict: `{verdict, actor, reason, ts}` |
| `execution` | What actually ran: `{status, stdout, before_state, after_state}` |
| `outcome` | Measured 5 minutes later: `{resolved, mttr_sec, regression}` |

`outcome` is what makes the autonomy argument possible. Promoting an action class from `ask` to
`auto` requires pointing at rows.

## Policy classes

Defined in [`policy/`](../../policy/), evaluated twice — once by the agent when composing a
proposal, once by the executor before acting.

| Class | Criteria | Examples |
|---|---|---|
| `auto` | `blast_radius=pod` ∧ `reversible=true` ∧ `confidence>0.9` ∧ evidence exists | restart a crashlooping pod |
| `ask` | everything not otherwise classified | patch a resource limit, scale a deployment, cordon a node |
| `never` | irreversible or outside the blast radius, regardless of confidence | delete a PVC or namespace, mutate IAM, terminate EC2, touch billing |

**Everything starts in `ask`.** Nothing is born `auto`.

## Model routing

Incidents are fingerprinted and matched by pgvector similarity against past incidents.

- **Match found, high confidence** → Gemma 3 1B locally. Effectively free.
- **Novel or low confidence** → escalate to Bedrock.

Both paths record token counts and cost, so the saving is measured rather than claimed.

Verify current Bedrock model IDs and pricing with the `claude-api` skill; do not hardcode
remembered figures.

## Runtime footprint

The node is a `t4g.medium`: 2 vCPU, 4 GB. Memory is the binding constraint and every addition is
budgeted against it.

| Component | RAM |
|---|---|
| k3s control plane + system | ~700 MB |
| Gemma 3 1B (q4) | ~900 MB |
| Postgres + pgvector | ~250 MB |
| kaval services (4 × ~120 MB) | ~480 MB |
| Prometheus (trimmed retention) | ~400 MB |
| Flux | ~100 MB |
| **Total** | **~2.8 GB** |
| Headroom | ~1.2 GB |

Choices this forced: Gemma **1B** not 4B; Flux rather than ArgoCD; Prometheus retention trimmed;
Loki deferred; Postgres in-cluster rather than RDS.

## Related

- [ADR-0002](../adr/0002-k3s-for-always-on-eks-as-a-chapter.md) — why k3s, why EKS is a chapter
- [ADR-0003](../adr/0003-aws-region.md) — region
- [budget-plan.md](../cost/budget-plan.md) — what all this costs
