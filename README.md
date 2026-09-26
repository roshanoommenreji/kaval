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

Live progress: [ROADMAP.md](ROADMAP.md) · **[Dashboard](https://claude.ai/code/artifact/c6ca9411-2b4b-47a8-81c1-3f3cc4c34d0c)** · [System](https://claude.ai/code/artifact/c6ca9411-2b4b-47a8-81c1-3f3cc4c34d0c#system) · [Journey](https://claude.ai/code/artifact/c6ca9411-2b4b-47a8-81c1-3f3cc4c34d0c#journey) · [Delivery](https://claude.ai/code/artifact/c6ca9411-2b4b-47a8-81c1-3f3cc4c34d0c#delivery)

Regenerate all of it with `make dashboard`.

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
| [docs/learn/](docs/learn/) | **The why.** One concept page per phase — theory, glossary, interview answers |
| [docs/releases/](docs/releases/) | Generated change records, one per production deploy |
| [ROADMAP.md](ROADMAP.md) | **The only place progress is recorded.** Everything else derives from it |
| [architecture.toml](architecture.toml) | The architecture as data — nodes, edges, and the phase each arrives in |
| [dashboard.toml](dashboard.toml) | Links and cost figures the dashboard can't derive from the repo |
| [services/](services/) | Application code — one directory per container; `shared/` holds the data model |
| [migrations/](migrations/) | Database schema migrations (Alembic), generated from `services/shared` |
| [inference/](inference/) | Gemma serving configuration |
| [mobile/](mobile/) | Expo / React Native operator console |
| [deploy/](deploy/) | Helm charts, per-environment values, Flux GitOps |
| [infra/](infra/) | Terraform modules and environments (`prod`, `staging`, `lab-eks`) |
| [policy/](policy/) | Rego action policies — what the agent may and may not do |
| [chaos/](chaos/) | Failure injection experiments |
| [evals/](evals/) | LLM eval harness and golden incident set |
| [scripts/ops/](scripts/ops/) | Operating AWS and the database — backup, restore, anonymise, cost report |
| [scripts/tracking/](scripts/tracking/) | Keeping Jira, Confluence and the dashboard in step with the repo |
| [scripts/dev/](scripts/dev/) | Local setup — Git hooks, new-lab scaffolding |
| [.github/workflows/](.github/workflows/) | CI and release pipelines |
| [CLAUDE.md](CLAUDE.md), [.claude/](.claude/) | How Claude Code works in this repo |

### Which tool lives where

| Tool | Home in this repo | Run with |
|---|---|---|
| Jira | `scripts/tracking/jira-sync.py` | `make jira EPIC=KAV-6` |
| Confluence | `scripts/tracking/publish-confluence.py` — a generated mirror, never edited by hand | `make docs-sync` |
| Dashboard | `scripts/tracking/dashboard.py` + `dashboard.toml` + `architecture.toml` | `make dashboard` |
| AWS | `infra/` (Terraform) · `scripts/ops/cost-report.sh` | `make plan` · `make cost-report` |
| Database | `services/shared/kaval_shared/models.py` · `migrations/` · `scripts/ops/` | `make migrate` · `make backup` |
| Secrets scanning | `.gitleaks.toml` · `scripts/dev/install-hooks.sh` | `make secrets-scan` |

Jira and Confluence credentials live in `.env` (gitignored; see `.env.example`). How branches
and commits work: [docs/contributing.md](docs/contributing.md).

---

## Three rules that shape everything

**1. Cost guardrails exist before compute does.** The budget alarms and the auto-shutdown Lambda
were the first infrastructure provisioned, before a single container ran. Ceiling is **$25/month**,
enforced by the system against itself.

**2. The agent cannot touch the cluster.** Reasoning is read-only. All mutation flows through the
executor, gated by policy. This is not a detail — it is the architecture.

**3. Nothing reaches prod without passing staging.** `local` → `staging` → `prod`, where staging is
a genuine second cluster created per release. The promotion gate refuses any artifact digest that
did not pass staging — and that refusal is tested. See
[ADR-0004](docs/adr/0004-environment-strategy-and-promotion.md).

---

## Licence

TBD before the repo goes public.
