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

Phases 1–3 run on a small AWS dev server (`t4g.medium`, about $5/month) that stops itself after
an idle hour ([ADR-0007](docs/adr/0007-develop-on-an-aws-dev-server.md)). The laptop stays the
editor.

```bash
make help          # every available target
make dev           # start the dev server if needed, then the stack on it:
                   #   Ollama + local model, Postgres, migrations, gateway (~4 min first time)
make dev-tunnel    # in a second terminal: gateway :8000, Postgres :5432, Ollama :11435
make signals SCENARIO=oom-crashloop   # write a fake incident's signals; then http://localhost:8000/docs
make bench         # measure the local model shortlist (docs/architecture/model-shortlist.md)
make dev-down      # stop the stack; the database and models are kept
make devbox-down   # stop the server (or let it stop itself after 1 h idle)
make test          # unit + policy tests
make lint          # ruff + mypy, the same target CI runs
make sync          # install exactly what uv.lock pins into .venv
make lock          # after editing pyproject.toml's dependencies; commit uv.lock with it
make cost-report   # what AWS is charging right now
```

See [docs/00-start-here.md](docs/00-start-here.md) before touching anything.

---

## Repository map

The short version is below. For every folder and file, what it does today and which phase fills
the empty ones, see **[docs/repo-guide.md](docs/repo-guide.md)**.

| Path | What lives there |
|---|---|
| [docs/](docs/) | **Start here.** Labs, ADRs, architecture, runbooks, cost, journal |
| [docs/learn/](docs/learn/) | **The why.** One concept page per phase — theory, glossary, interview answers |
| [docs/releases/](docs/releases/) | Generated change records, one per production deploy |
| [ROADMAP.md](ROADMAP.md) | **The only place progress is recorded.** Everything else derives from it |
| [docs/architecture/architecture.toml](docs/architecture/architecture.toml) | The architecture as data — nodes, edges, and the phase each arrives in |
| [scripts/tracking/dashboard.toml](scripts/tracking/dashboard.toml) | Links and cost figures the dashboard can't derive from the repo |
| [services/](services/) | Application code — one directory per container; `shared/` holds the data model |
| [migrations/](migrations/) | Database schema migrations (Alembic), generated from `services/shared` |
| [inference/](inference/) | Gemma serving configuration |
| [mobile/](mobile/) | Expo / React Native operator console |
| [deploy/](deploy/) | Helm charts, per-environment values, Flux GitOps |
| [infra/](infra/) | Terraform modules and environments (`dev`, `staging`, `prod`, `lab-eks`) |
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
| Dashboard | `scripts/tracking/dashboard.py` + `scripts/tracking/dashboard.toml` + `docs/architecture/architecture.toml` | `make dashboard` |
| AWS | `infra/` (Terraform) · `scripts/ops/cost-report.sh` | `make plan` · `make cost-report` |
| Dev server | `infra/envs/dev` + `infra/modules/devbox` | `make devbox-up` · `devbox-ssh` · `devbox-down` |
| Database | `services/shared/kaval_shared/models.py` · `migrations/` · `scripts/ops/` | `make migrate` · `make backup` |
| Secrets scanning | `.gitleaks.toml` · `scripts/dev/install-hooks.sh` | `make secrets-scan` |
| Commit convention | `scripts/dev/check_commits.py` (the `commit-msg` hook and CI) | `./scripts/dev/install-hooks.sh` once |
| GitHub ↔ Jira | the GitHub for Jira app, linked by the `KAV-<n>` key ([ADR-0011](docs/adr/0011-commit-convention-and-jira-link.md)) | nothing: it's automatic |

Jira and Confluence credentials live in `.env` (gitignored; see `.env.example`). How branches
and commits work: [docs/contributing.md](docs/contributing.md).

---

## Three rules that shape everything

**1. Cost guardrails exist before compute does.** The budget alarms and the auto-shutdown Lambda
were the first infrastructure provisioned, before a single container ran. Ceiling is **$40/month**
(raised from $25 by [ADR-0008](docs/adr/0008-production-database-on-its-own-server.md) when the database got its own server),
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
