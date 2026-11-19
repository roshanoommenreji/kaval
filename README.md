# Kaval

**കാവൽ** — *the watch.*

[![CI](https://github.com/roshanoommenreji/kaval/actions/workflows/ci.yml/badge.svg)](https://github.com/roshanoommenreji/kaval/actions/workflows/ci.yml)
[![Licence: MIT](https://img.shields.io/badge/licence-MIT-blue.svg)](LICENSE)

**An operations agent that is allowed to think, but not allowed to act.**

Kaval watches a Kubernetes cluster and an AWS bill. When something breaks or money is being
wasted, a small language model that runs on the cluster's own server reads the evidence and
writes down what it thinks happened and what to do about it. Nothing changes until a person
presses **Approve** on a Slack message, on a laptop or a phone.

It is a one-person learning project, built in public and documented as it went, so that the
reasoning behind each choice is as readable as the code. It runs for real on AWS, but it is
parked most of the time to keep the bill small. The honest list of what it does not do is
[further down](#what-kaval-does-not-do).

```
signals ──▶ correlate ──▶ context ──▶ LLM ──▶ proposal ──▶ policy ──▶ approve? ──▶ execute ──▶ measure
   │                          │                               │          │                        │
 k8s events              runbook RAG                      auto/ask/    Slack                  outcome
 prometheus              past incidents                     never       card                  recorded
 cost explorer           recent changes                                                            │
                                                                                                   ▼
                                                                                        feeds the next context
```

Two kinds of trouble go through the one machine: **reliability** (a pod keeps crashing, a
release is bad, a disk is filling) and **cost** (an idle volume, a forgotten snapshot).

---

## The design in three sentences

1. **The part that thinks has no power.** The `agent` service has read-only credentials and its
   only possible output is a row in a database table. A separate `executor` holds narrow write
   permissions and acts only on a proposal that passed policy and, where required, a human.
   A confused or manipulated model can therefore only *suggest* harm, which policy and a person
   then reject. If a change would give `agent` write access, the change is wrong.
2. **Autonomy is earned, with evidence.** Every action is classed `auto`, `ask` or `never`.
   Everything starts as `ask`. Each proposal, decision and measured outcome is stored and never
   edited, and moving a class to `auto` has to be argued from those rows. The claim of the
   project is not "AI fixed my server". It is "here is how much evidence it took before I let it".
3. **Money is a design constraint, not a bill you read later.** The ceiling is **$50 a month**,
   enforced by alarms and a shut-off that were built before any compute existed.

---

## What has been shown to work

Each line links to the page that records it. Numbers are copied from those pages, not rounded up.

| Claim | Evidence |
|---|---|
| **A real fault was found, diagnosed and fixed with a human in the loop.** A crashing service on staging was detected, diagnosed by Kaval's own model, approved by a person and fixed, and the fix held. | [Game day, 2026-10-15](docs/postmortems/2026-10-15-oom-diagnosed-by-itself.md): 103 s of machine time, 194 s on the clock including the person. All seven runs: [mttr.svg](docs/architecture/mttr.svg) |
| **Five kinds of failure were caused on purpose, on staging.** A crashing service, a bad release, a lost server, a slow service and a full disk. | [Post-mortems](docs/postmortems/README.md); the experiments are in [chaos/](chaos/) |
| **The model's answers are scored, and a dangerous suggestion fails the build.** Twenty golden incidents, plus ten prompt-injection cases where the incident text itself tries to give the model orders. | [evals/](evals/README.md); the threat model: [threat-model.md](docs/architecture/threat-model.md) |
| **Only a signed build can run.** Every image is scanned, signed and given a parts list when it is built. The promotion step refuses an image whose signature does not verify, and the cluster itself refuses an unsigned pod, on staging and on production. | [ADR-0092](docs/adr/0092-promotion-refuses-an-image-whose-signature-does-not-verify.md), [ADR-0094](docs/adr/0094-the-cluster-refuses-unsigned-images-on-staging-and-fails-closed.md), [ADR-0095](docs/adr/0095-production-refuses-unsigned-images-from-its-next-start-up.md), [Lab 97](docs/labs/lab-97-production-refuses-unsigned-images.md) |
| **A person decided, in Slack, on a real production question.** On 2026-11-16 two Approve presses were carried out (both forgotten snapshots were removed from AWS by a separate removal worker) and four Deny presses were recorded with nothing changed. | [Lab 64, Part 5](docs/labs/lab-64-closing-phase-6-on-production.md) |
| **Nothing reaches production without passing staging.** The build is made once and the same artifact is promoted. A promotion must name a tagged release. | [ADR-0004](docs/adr/0004-environment-strategy-and-promotion.md), [ADR-0032](docs/adr/0032-promote-workflow-and-the-promotion-guard.md), [ADR-0081](docs/adr/0081-a-promotion-must-be-a-tagged-release.md) |
| **A person who does not answer is not ignored.** An unanswered request is reminded at 15 minutes, escalated at 30 and expires at 4 hours, with nothing done. The approval route needs a login. | [ADR-0048](docs/adr/0048-approvals-nobody-answers-expiry-and-escalation.md), [ADR-0073](docs/adr/0073-the-approval-route-gets-a-login-and-the-namespace-gets-network-rules.md) |
| **The database can be restored.** Dumps are taken when production parks, and a restore drill puts the newest one into a throwaway database and times it. | [Drills](docs/drills/README.md), [disaster recovery](docs/architecture/disaster-recovery.md) |
| **The bill stays small.** September: $1.39 of usage. October: $15.31 of usage with production brought up and parked several times. Both were covered by credits. | [Actuals](docs/cost/actuals/), [budget plan](docs/cost/budget-plan.md) |

---

## How it is built

| Part | What it does | Can it change things? |
|---|---|---|
| `collector` | Reads Kubernetes events, Prometheus and Cost Explorer; writes signals | No, read-only |
| `agent` | Groups signals into incidents, builds context from runbooks and past incidents, asks the model, writes a validated proposal | **No, read-only** |
| `policy/` | Rego rules that class each proposed action `auto`, `ask` or `never` | Decides, does nothing |
| `gateway` | The API, and the Slack card with its Approve and Deny buttons | Records a decision only |
| `executor` | Carries out an approved action, records what it did, measures 5 minutes later whether the fault is gone | **The only part that can** |
| Gemma 3 1B (Ollama) | The model, on CPU, on the same server | No |
| Postgres + pgvector | Signals, incidents, proposals, decisions, executions, outcomes; the runbook search index | Rows are added, never changed (one field, an incident's close time, is the only exception) |

It runs on **k3s on one small Graviton server, with the database on a second one** (no load balancer, no NAT gateway, no managed
control plane, all deliberately: [what they would cost](docs/cost/budget-plan.md)). Environments
differ only by a Helm values file and a pinned image digest: `local` → `staging` → `prod`. Staging
is built fresh for a release and deleted afterwards. The same chart also ran, unchanged, on EKS for
a few hours in a chapter on portability ([Lab 66](docs/labs/lab-66-the-eks-lab-live-two-sessions-and-what-they-found.md)).

Removal of wasted resources is done by a **separate worker outside the cluster**, so the cluster
never holds the key to delete anything in AWS ([ADR-0063](docs/adr/0063-waste-is-removed-in-steps-by-a-separate-worker-and-only-with-approval.md)).

Diagrams that are generated from [architecture.toml](docs/architecture/architecture.toml):
**[System](https://claude.ai/code/artifact/c6ca9411-2b4b-47a8-81c1-3f3cc4c34d0c#system)** ·
**[Incident journey](https://claude.ai/code/artifact/c6ca9411-2b4b-47a8-81c1-3f3cc4c34d0c#journey)** ·
[written overview](docs/architecture/overview.md).

---

## What Kaval does not do

Said plainly, because a project like this is easy to oversell.

- **The model is small on purpose.** Gemma 3 1B is cheap and runs on a CPU. It is not as good as a
  large model, and the evals show where it is weak. A person approves everything that changes
  something. A live hand-off to a larger model is parked ([ADR-0098](docs/adr/0098-phase-2-closes-jev-and-live-bedrock-escalation-move-to-future-scope.md)).
- **It is one person's project.** One operator, one AWS account, one Slack channel. There is no
  on-call rota, no multi-tenant design and no promise of an uptime.
- **Production is parked most of the time.** It is brought up for a session and stopped again, and
  a timer stops it at night. That is a cost decision, and it means the long-running behaviour of an
  always-on system has been tested in sessions rather than in months.
- **There is no phone app.** One was planned, then dropped: Slack on a phone already shows the
  buttons ([ADR-0097](docs/adr/0097-phase-9-is-retired-slack-is-the-approval-surface.md)).
- **It does not yet act automatically on anything.** Every action class is `ask`. Promoting one to
  `auto` needs evidence that does not exist yet.
- **Parked on purpose:** a demo video, and a nightly check for changes made by hand in the AWS
  account, are in [future scope](docs/future-scope.md) (FS-2 and FS-17) with what would bring each
  back. Version 1 closed on 2026-11-19; [the retrospective](docs/retrospective-v1.md) says what
  went to plan and what did not.

---

## Status

| | |
|---|---|
| **Phase** | Version 1 is complete (2026-11-19): Phases 0–8 are done and Phase 9 (a phone app) was retired. See [the retrospective](docs/retrospective-v1.md) and [ADR-0105](docs/adr/0105-version-1-closes-and-the-nightly-drift-check-moves-to-future-scope.md) |
| **Started** | 2026-08-22 |
| **Version 1** | Closed 2026-11-19, ahead of its March 2027 target |
| **Running cost** | $0.00 billed through October. Usage was $1.39 in September and $15.31 in October, all covered by credits. Hard limit $50 a month |
| **Visibility** | Public since 2026-09-30 (pulled forward from v1 on purpose) |
| **Size** | 107 hands-on [labs](docs/labs/), 105 [decision records](docs/adr/), 28 [runbooks](docs/runbooks/), a [journal](docs/journal/) of every working day |

Live progress: [ROADMAP.md](ROADMAP.md) · **[Dashboard](https://claude.ai/code/artifact/c6ca9411-2b4b-47a8-81c1-3f3cc4c34d0c)** · [Delivery](https://claude.ai/code/artifact/c6ca9411-2b4b-47a8-81c1-3f3cc4c34d0c#delivery)

Regenerate all of it with `make dashboard`.

---

## Where to read first

| If you want | Start at |
|---|---|
| The idea and the three rules, in four minutes | [docs/00-start-here.md](docs/00-start-here.md) |
| Why each choice was made | [docs/adr/](docs/adr/) — one page per decision, with what was rejected |
| To redo any step yourself, from nothing | [docs/labs/](docs/labs/) — each lab says what to run and what you should see |
| The concepts behind a phase, with interview answers | [docs/learn/](docs/learn/) |
| What happened on the days things went wrong | [docs/postmortems/](docs/postmortems/) and [docs/journal/](docs/journal/) |
| What an operator does when an alert fires | [docs/runbooks/](docs/runbooks/) — these are also what the agent searches |
| What can go wrong with the system itself | [threat model](docs/architecture/threat-model.md), [access review](docs/architecture/access-review.md) |
| Who owns what and when keys change | [docs/ownership.md](docs/ownership.md), [secret rotation](docs/architecture/secret-rotation.md) |

---

## Try it

The first three phases run on a small AWS dev server (`t4g.medium`, about $5 a month) that stops
itself after an idle hour ([ADR-0007](docs/adr/0007-develop-on-an-aws-dev-server.md)). The laptop
stays the editor. You need an AWS account and its login on your machine; creating anything bills
you, so read [the budget plan](docs/cost/budget-plan.md) first.

```bash
make help          # every available target
make dev           # start the dev server if needed, then the stack on it:
                   #   Ollama + local model, Postgres, migrations, gateway (~4 min first time)
make dev-tunnel    # in a second terminal: gateway :8000, Postgres :5432, Ollama :11435
make signals SCENARIO=oom-crashloop   # write a fake incident's signals; then http://localhost:8000/docs
make correlate                        # group them into an incident (/v1/incidents)
make index-runbooks                   # sync docs/runbooks/ into the retrieval index
make context INCIDENT=<uuid>          # see what a diagnosis would be given: runbooks, history, changes
make diagnose INCIDENT=<uuid>         # ask the local model to diagnose it; writes a proposal or nothing
make policy-check TYPE=restart_pod BLAST_RADIUS=pod CONFIDENCE=0.95 REVERSIBLE=1  # classify one hypothetical action, no database
make evals         # score the 20 golden incidents (evals/README.md); ONLY=name for one case
make bench         # measure the local model shortlist (docs/architecture/model-shortlist.md)
make dev-down      # stop the stack; the database and models are kept
make devbox-down   # stop the server (or let it stop itself after 1 h idle)
make test          # unit + policy tests
make lint          # ruff + mypy, the same target CI runs
make sync          # install exactly what uv.lock pins into .venv
make lock          # after editing pyproject.toml's dependencies; commit uv.lock with it
make cost-report   # what AWS is charging right now
make tag-scan      # read-only: which Kaval resources in the account are missing a required tag
make iam-review    # read-only: every Kaval IAM role, the wide statements, Access Analyzer, the laptop user
```

See [docs/00-start-here.md](docs/00-start-here.md) before touching anything. Staging and
production are run by [runbooks](docs/runbooks/prod-rollout.md), not by `make dev`.

---

## Repository map

The short version is below. For every folder and file, what it does today, see
**[docs/repo-guide.md](docs/repo-guide.md)**.

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
| [deploy/](deploy/) | Helm charts, per-environment values, Flux GitOps |
| [infra/](infra/) | Terraform modules and environments (`dev`, `staging`, `prod`, `lab-eks`) |
| [policy/](policy/) | Rego action policies — what the agent may and may not do |
| [chaos/](chaos/) | Failure injection experiments (game days on staging: memory, a bad release, a lost server, a slow service, a full disk); write-ups in [docs/postmortems/](docs/postmortems/) |
| [evals/](evals/) | LLM eval harness and golden incident set |
| [scripts/ops/](scripts/ops/) | Operating AWS and the database — backup, restore, anonymise, cost report |
| [scripts/tracking/](scripts/tracking/) | Keeping Jira, Confluence and the dashboard in step with the repo |
| [scripts/release/](scripts/release/) | Moving a build between environments — the staging image pin that `release.yml` uses and the promotion gate behind `promote.yml`, and the staging smoke test that records a pass |
| [scripts/dev/](scripts/dev/) | Local setup — Git hooks, new-lab scaffolding |
| [.github/workflows/](.github/workflows/) | CI and release pipelines |
| [CLAUDE.md](CLAUDE.md), [.claude/](.claude/) | How Claude Code works in this repo |

<details>
<summary><strong>Which tool lives where</strong></summary>

| Tool | Home in this repo | Run with |
|---|---|---|
| Jira | `scripts/tracking/jira-sync.py` (stories, UAT verdicts) · `scripts/tracking/jira-dashboards.toml` (the Delivery, UAT and Releases dashboards) | `make jira EPIC=KAV-6` · `make jira-dashboards` |
| Confluence | `scripts/tracking/publish-confluence.py` — a generated mirror, never edited by hand | `make docs-sync` |
| Dashboard | `scripts/tracking/dashboard.py` + `scripts/tracking/dashboard.toml` + `docs/architecture/architecture.toml` | `make dashboard` |
| AWS | `infra/` (Terraform; its record lives in a private S3 bucket, `infra/envs/state`) · `scripts/ops/cost-report.sh` | `make tf-init ENV=prod` · `make plan` · `make cost-report` |
| Dev server | `infra/envs/dev` + `infra/modules/devbox` | `make devbox-up` · `devbox-ssh` · `devbox-down` |
| Staging | `infra/envs/staging` + `infra/modules/idle-stop` + `scripts/ops/staging.sh` | `make staging-up` · `staging-status` · `staging-down` |
| EKS lab (ephemeral) | `infra/envs/lab-eks` + `infra/modules/eks-lab` + `infra/modules/lab-reaper` + `scripts/ops/lab_sweep.py` | `make lab-plan` · `lab-up` · `lab-down` · `lab-sweep` |
| Database | `services/shared/kaval_shared/models.py` · `migrations/` · `scripts/ops/` | `make migrate` · `make backup` |
| Secrets scanning | `.gitleaks.toml` · `scripts/dev/install-hooks.sh` | `make secrets-scan` |
| Commit convention | `scripts/dev/check_commits.py` (the `commit-msg` hook and CI) | `./scripts/dev/install-hooks.sh` once |
| GitHub ↔ Jira | the GitHub for Atlassian app, linked by the `KAV-<n>` key ([ADR-0011](docs/adr/0011-commit-convention-and-jira-link.md)) | nothing: it's automatic |

Jira and Confluence credentials live in `.env` (gitignored; see `.env.example`). How branches
and commits work: [docs/contributing.md](docs/contributing.md).

</details>

---

## Security

The repository is public, so its **history** is scanned for secrets on every pull request, and no
account number, endpoint or key is ever committed. To report a vulnerability, use GitHub's private
form, not a public issue: see [SECURITY.md](.github/SECURITY.md).

## Licence

[MIT](LICENSE).
