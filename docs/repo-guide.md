# Guide to this repository

Every folder and file in Kaval: what it is, what it does today, and what it will do later.

Many folders are still empty on purpose. The whole structure was laid out on day one, so every
phase has a place waiting for it. Each entry below has one of three statuses:

- **Working**: exists and is in use now
- **Written, not yet run**: the code or document exists, but what it depends on hasn't been
  built yet
- **Placeholder**: an empty folder that holds its place (the `.gitkeep` file inside only exists
  because Git can't store an empty folder). The phase that fills it is named.

---

## The big picture

The repository holds four kinds of thing:

```
Kaval/
│
├── THE PRODUCT ─────────── the software that watches, diagnoses and fixes
│   ├── services/            the Python programs (one per container)
│   ├── migrations/          how the database gets its tables
│   ├── inference/           how the AI model (Gemma) is served
│   ├── policy/              the rules for what the AI is allowed to do
│   └── mobile/              the phone app
│
├── WHERE IT RUNS ────────── the machines and how software gets onto them
│   ├── infra/               Terraform: creates things in AWS
│   └── deploy/              Helm + Flux: puts the software onto Kubernetes
│
├── PROVING IT WORKS ─────── testing beyond ordinary unit tests
│   ├── evals/               is the AI's diagnosis actually right?
│   └── chaos/               break things on purpose, watch it heal
│
└── RECORDS AND TOOLING ──── how the project is tracked, explained and kept honest
    ├── docs/                every document: labs, decisions, journal, concepts
    ├── scripts/             helper programs (AWS, database, Jira/Confluence/dashboard)
    ├── ROADMAP.md           the ONLY place progress is recorded
    └── root config files    Makefile, pyproject.toml, .env.example, ...
```

One rule explains a lot of the layout: **the repository is the single source of truth.** Jira,
Confluence and the dashboard are generated *from* it by the scripts in `scripts/tracking/`, and
are never edited by hand. If they disagree with the repo, the repo is right.

---

## Files at the top level

| File | What it is | Status |
|---|---|---|
| `README.md` | The front page: what Kaval is, current status, quick start, and a short map | Working |
| `ROADMAP.md` | All 10 phases as checklists. **The only place progress is recorded.** The dashboard, Jira mirror and architecture diagrams all read their status from here | Working |
| `CLAUDE.md` | Instructions for Claude Code: the cost ceiling, the security rules, conventions and the Definition of Done | Working |
| `Makefile` | Short commands for everything (`make help` lists them): `make test`, `make migrate`, `make docs-sync`, `make jira`, `make plan`... | Working. Some targets wait on later phases (see below) |
| `pyproject.toml` | Python project settings: dependencies, and the rules for `ruff`, `mypy` and `pytest` | Working |
| `alembic.ini` | Settings for Alembic, the database migration tool. Points it at `migrations/` | Working |
| `architecture.toml` | The system's architecture written as data: every component and connection, and which phase it arrives in. The dashboard draws the three diagrams from this | Working |
| `dashboard.toml` | Things the dashboard can't work out by itself: links, cost figures, per-phase Jira/Confluence links | Working |
| `.env.example` | A template listing every setting and secret the project needs, with fake values. Copy it to `.env` and fill it in | Working |
| `.env` | **Your real settings and secrets** (Jira token, database password). Not in Git, and never will be | Working, local only |
| `.gitignore` | Tells Git which files never to save: secrets, Terraform state, caches, generated files | Working |
| `.gitattributes` | Line-ending rules. Stops Windows from adding `\r` characters that break shell scripts on Linux | Working |
| `.gitleaks.toml` | Rules for gitleaks, the scanner that blocks any commit containing a secret | Working |

## `.claude/` and `.github/`

| Path | What it is | Status |
|---|---|---|
| `.claude/settings.json` | Which read-only commands (e.g. `terraform plan`, `kubectl get`) Claude Code may run without asking you each time | Working |
| `.claude/skills/` | Project-specific Claude Code skills, e.g. a `cost-check` skill | Placeholder, no phase set |
| `.github/workflows/` | Automated pipelines. `ci.yml` runs tests and scans on every change. `release.yml`, `promote.yml` and `rollback.yml` move a build through staging to production | Placeholder. CI in Phase 1 (`KAV-24`); the release pipeline in Phase 4 |

---

## The product

### `services/`: the Python programs

Each subfolder becomes one container. The most important design rule lives here: **the part
that thinks (`agent`) cannot change anything; the part that changes things (`executor`) does not
think.**

| Path | What it will do | Status |
|---|---|---|
| `services/shared/` | Code every service shares. Today that's the database layer | Working |
| `services/shared/kaval_shared/models.py` | The seven database tables that record each incident from start to finish: `signal → incident → proposal → action → decision → execution → outcome`. Rows are added, never edited, so the tables are the audit trail | Working |
| `services/shared/kaval_shared/db.py` | Opens the database connection from the `POSTGRES_*` settings in `.env` | Working |
| `services/shared/tests/` | Tests for the tables: one checks their shape, one writes a full incident through all seven | Working |
| `services/collector/` | Gathers raw observations (Kubernetes events, Prometheus metrics, AWS cost data) and saves them as `signal` rows. Read-only | Placeholder. Fake signals in Phase 1 (`KAV-23`); real ones in Phase 3, cost data in Phase 7 |
| `services/agent/` | Groups signals into incidents, asks the AI for a diagnosis, and writes a `proposal`. **Read-only credentials; it can only suggest** | Placeholder, Phase 2 |
| `services/executor/` | The **only** component allowed to change the cluster or AWS. Acts only on proposals that passed the policy check and, where required, human approval | Placeholder, Phase 3 |
| `services/gateway/` | The web API (FastAPI) the phone app talks to: list proposals, approve or deny, push notifications | Placeholder. Skeleton in Phase 1 (`KAV-23`); the phone features in Phase 5 |

### `migrations/`: how the database gets its tables

| Path | What it is | Status |
|---|---|---|
| `migrations/versions/` | One file per change to the database structure. The first one creates the seven tables | Working |
| `migrations/env.py` | Connects Alembic to the database and to `models.py` | Working |
| `migrations/script.py.mako` | Template for new migration files | Working |

Run `make migrate` to bring a database up to date.

### `inference/`, `policy/`, `mobile/`

| Path | What it will do | Status |
|---|---|---|
| `inference/` | Settings for serving the Gemma 3 1B model through Ollama | Placeholder, Phase 1 (`KAV-22`) |
| `policy/` | The rules for every proposed action: `auto` (do it), `ask` (a human decides) or `never` (refused). Checked twice, once by the agent and again by the executor. `README.md` explains the design | Design written; rules in Phase 2 |
| `mobile/` | The Android phone app (Expo / React Native): see incidents, read the AI's explanation, tap Approve or Deny | Placeholder, Phase 5 |

---

## Where it runs

### `infra/`: Terraform, the things created in AWS

**Modules** are reusable building blocks. **Environments** assemble modules for one purpose.

| Path | What it is | Status |
|---|---|---|
| `infra/modules/budget/` | Budget alarms at $18 and $22, and a Lambda function that shuts compute down at $24. Built **before** anything that can cost money. `lambda/hard_stop.py` is that function | Working, applied in AWS |
| `infra/modules/network/` | The VPC, subnets and firewall rules. No NAT Gateway, which alone would cost $32/month | Placeholder, Phase 4 |
| `infra/modules/node/` | The single cheap `t4g.medium` spot server that runs k3s | Placeholder, Phase 4 |
| `infra/modules/ecr/` | Where container images are stored in AWS | Placeholder, Phase 4 |
| `infra/modules/iam/` | Permissions, including the narrow write access only the executor gets | Placeholder, Phase 4 |
| `infra/modules/eks-lab/` | A real Amazon EKS cluster, created briefly to prove the same software runs there, then destroyed | Placeholder, Phase 8 |
| `infra/envs/prod/` | The production environment. Today it contains only the budget module; the compute modules are written in but commented out | Working (budget only) |
| `infra/envs/prod/main.tf`, `variables.tf`, `outputs.tf` | What to create, its settings, and what it reports back | Working |
| `infra/envs/prod/terraform.tfvars.example` | Template for your real values (`terraform.tfvars` itself is not in Git) | Working |
| `infra/envs/prod/.terraform.lock.hcl` | Pins exact provider versions, so every run uses the same ones | Working |
| `infra/envs/staging/` | A second, temporary copy of production for testing each release. Created on demand for about $1/month | Placeholder, Phase 4 |
| `infra/envs/lab-eks/` | Uses the `eks-lab` module | Placeholder, Phase 8 |

### `deploy/`: getting the software onto Kubernetes

| Path | What it will do | Status |
|---|---|---|
| `deploy/charts/kaval/` | One Helm chart that describes how to run every Kaval service on Kubernetes | Placeholder, Phase 3 |
| `deploy/environments/local/`, `staging/`, `prod/`, `lab-eks/` | One settings file per environment. **These are the only differences between environments.** The code and the chart are identical everywhere (ADR-0004) | Placeholder. Phase 3; `lab-eks` in Phase 8 |
| `deploy/gitops/` | Flux configuration. The cluster pulls its setup from Git and rebuilds itself if the server is lost | Placeholder, Phase 4 |

---

## Proving it works

| Path | What it will do | Status |
|---|---|---|
| `evals/` | A set of 20 known incidents with known right answers. Checks that the AI's diagnoses are correct, not just confident | Design in `README.md`; built in Phase 2 |
| `chaos/` | Scheduled experiments that break things on purpose (kill a pod, fill a disk) inside a fenced-off area, to prove Kaval heals them. Measures time to recovery | Design in `README.md`; built in Phase 6 |

---

## Records and tooling

### `docs/`: every document

| Path | What it is | Status |
|---|---|---|
| `docs/00-start-here.md` | The first thing to read: what Kaval is, the three rules behind it, how to find your way around | Working |
| `docs/repo-guide.md` | This file | Working |
| `docs/contributing.md` | How branches, commits and merging work | Working |
| `docs/future-scope.md` | Ideas deliberately left out of the plan for after v1 | Working |
| `docs/labs/` | **Step-by-step guides**, one per session, detailed enough for a stranger to repeat from zero. Also mirrored to Confluence | Working (Labs 00–02) |
| `docs/learn/` | **Concept pages**, one per phase: *why* things work the way they do, glossary, self-check questions, interview answers. Each is marked "written from theory" until the phase is done, then rewritten from experience | Working (all 11 written) |
| `docs/adr/` | **Architecture Decision Records**: each big decision, why it was made, what was rejected. Numbered, never deleted | Working (0001–0006) |
| `docs/journal/` | **One dated entry per work session**: what was done, what broke, what's still open. The "Open threads" section of the newest entry feeds the dashboard's Blockers panel | Working |
| `docs/architecture/overview.md` | How the system fits together, in words and sketches | Working |
| `docs/architecture/diagrams/` | Exported diagram images. The live diagrams come from `architecture.toml` and appear on the dashboard | Placeholder, Phase 9 |
| `docs/runbooks/` | **Troubleshooting guides**, one per failure type. Written for humans, **and** the AI agent reads them when diagnosing incidents | Working (1 runbook); more in Phase 6 |
| `docs/cost/budget-plan.md` | Every expected cost, the $25/month ceiling, and how it's enforced | Working |
| `docs/cost/actuals/` | The real bill, one file per month | Placeholder. Starts with the first real spend, in Phase 4 |
| `docs/releases/` | A change record for every production release, generated automatically: what changed, who approved it, how to roll back | Format in `README.md`; the first record comes in Phase 4 |
| `docs/course/outline.md` | The plan for turning the labs into a course or video series | Working |
| `docs/course/episode-scripts/` | Scripts for each episode | Placeholder, Phase 9 |
| `docs/dashboard.html` | The generated dashboard page. Not in Git; the published copy is the real one | Generated |

### `scripts/`: helper programs, grouped by what they touch

| Path | What it does | Status |
|---|---|---|
| **`scripts/ops/`** | **AWS and the database** | |
| `scripts/ops/cost-report.sh` | Month-to-date AWS spend against the $25 ceiling (`make cost-report`) | Working |
| `scripts/ops/backup.sh` | Nightly database dump to S3 (`make backup`) | Written, not yet run. Needs the production database from Phase 4 |
| `scripts/ops/restore.sh` | Restores a backup into staging (made anonymous first) or, in an emergency, into production | Written, not yet run (Phase 4) |
| `scripts/ops/anonymise.sql` | Strips personal and secret data from a copy before staging gets it | Written, not yet run (Phase 4) |
| **`scripts/tracking/`** | **Jira, Confluence and the dashboard** | |
| `scripts/tracking/dashboard.py` | Builds the progress dashboard from the repo (`make dashboard`) | Working |
| `scripts/tracking/publish-confluence.py` | Publishes the repo's docs to the Confluence space (`make docs-sync` runs both) | Working |
| `scripts/tracking/jira-sync.py` | Lists, moves and creates Jira stories (`make jira EPIC=KAV-6`) | Working |
| `scripts/tracking/atlassian.py` | The Jira/Confluence login code, shared by the two scripts above | Working |
| **`scripts/dev/`** | **Setting up your own machine** | |
| `scripts/dev/install-hooks.sh` | Installs the pre-commit check that blocks secrets | Working |
| `scripts/dev/new-lab.sh` | Creates a new lab document and journal entry from a template (`make lab NAME=...`) | Working |

---

## Where does a new thing go?

| You're adding... | Put it in |
|---|---|
| A new Python service | `services/<name>/` |
| A change to database tables | `services/shared/kaval_shared/models.py`, then a new file in `migrations/versions/` |
| Something new in AWS | a module in `infra/modules/`, used from `infra/envs/<env>/` |
| A setting that differs between environments | `deploy/environments/<env>/`, **never** a code change |
| A decision you had to think about | a new numbered ADR in `docs/adr/` |
| How-to steps for what you just did | `docs/labs/` |
| How to fix a failure | `docs/runbooks/`. The agent will read it too |
| A script for AWS or the database | `scripts/ops/` |
| A script for Jira, Confluence or the dashboard | `scripts/tracking/` |
| A new secret or setting | a fake value in `.env.example`, the real one in `.env` |

## What is deliberately not in Git

| What | Why |
|---|---|
| `.env` | Secrets. The repo goes public at v1, and Git history is permanent |
| `infra/envs/prod/terraform.tfvars` | Your real email and settings |
| `infra/envs/prod/terraform.tfstate` | Terraform's record of what it created in AWS; it can contain account details. **Only copy is on this laptop**, so it moves to S3 later |
| `.venv/` | Installed Python packages. Rebuild with `pip install -e ".[dev]"` |
| `docs/dashboard.html` | Regenerated every time |
| Caches (`__pycache__`, `.mypy_cache`, `.terraform/`...) | Rebuilt automatically |

---

When a folder is added, moved or starts being used, update this guide in the same change.
