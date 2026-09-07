# Kaval

**കാവൽ** — *the watch.*

An autonomous operations agent that watches a Kubernetes cluster and an AWS bill, diagnoses
problems with a self-hosted LLM, proposes fixes, and executes them only after a human approves
from their phone.

Two signal domains — **reliability** and **cost** — through one machine.

```
signals ──▶ correlate ──▶ context ──▶ LLM ──▶ proposal ──▶ policy ──▶ approve? ──▶ execute ──▶ measure
   │                          │                               │          │                        │
 k8s events              runbook RAG                      auto/ask/    phone                  outcome
 prometheus              past incidents                     never       push                  recorded
 cost explorer           recent changes                                                            │
                                                                                                   ▼
                                                                                        feeds the next context
```

---

## Status

| | |
|---|---|
| **Phase** | 0 — Foundations |
| **Started** | 2026-08-22 |
| **Target v1** | ~March 2027 |
| **Running cost** | $0 (nothing provisioned yet) |
| **Visibility** | Private until v1 |

Live progress: [ROADMAP.md](ROADMAP.md)

---

## The idea in one paragraph

Most "AI ops" demos let a model run commands against your cluster and hope for the best. Kaval
does the opposite: the component that *thinks* has read-only credentials and can only write rows
to a database. A separate executor, holding narrowly scoped permissions, acts only on proposals
that passed a policy check and — initially — a human tap. Every proposal, decision, and measured
outcome is recorded immutably. Autonomy is then *earned*: as evidence accumulates that a class of
action is safe, it graduates from `ask` to `auto`, and the data justifying that is in the table.

---

## Quick start

Nothing here costs money yet. Phases 0–3 run entirely on your laptop.

```bash
make help          # every available target
make dev           # local stack: Ollama + Gemma, Postgres, gateway, signal generator
make test          # unit + policy tests
make cost-report   # what AWS is charging right now
```

See [docs/00-start-here.md](docs/00-start-here.md) before touching anything.

---

## Repository map

| Path | What lives there |
|---|---|
| [docs/](docs/) | **Start here.** Labs, ADRs, architecture, runbooks, cost, journal |
| [services/](services/) | Application code — one directory per container |
| [inference/](inference/) | Gemma serving configuration |
| [mobile/](mobile/) | Expo / React Native operator console |
| [deploy/](deploy/) | Helm charts, per-environment values, Flux GitOps |
| [infra/](infra/) | Terraform modules and environments |
| [policy/](policy/) | Rego action policies — what the agent may and may not do |
| [chaos/](chaos/) | Failure injection experiments |
| [evals/](evals/) | LLM eval harness and golden incident set |

---

## Two rules that shape everything

**1. Cost guardrails exist before compute does.** The budget alarms and the auto-shutdown Lambda
were the first infrastructure provisioned, before a single container ran. Ceiling is **$25/month**,
enforced by the system against itself.

**2. The agent cannot touch the cluster.** Reasoning is read-only. All mutation flows through the
executor, gated by policy. This is not a detail — it is the architecture.

---

## Licence

TBD before the repo goes public.
