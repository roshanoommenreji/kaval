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
│   ├── inference/           how the AI model is served (settings in compose.yaml for now)
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
| `Makefile` | Short commands for everything (`make help` lists them): `make dev`, `make signals`, `make test`, `make lint`, `make sync`, `make lock`, `make migrate`, `make docs-sync`, `make jira`, `make plan`... | Working. Some targets wait on later phases (see below) |
| `uv.lock` | The **exact** version and file hashes of every Python package (46), generated from `pyproject.toml` by `make lock`. CI, the laptop (`make sync`) and both images install from it, so all three run the same code. Never edited by hand | Working (`KAV-24`) |
| `pyproject.toml` | Python project settings: dependencies, the rules for `ruff`, `mypy` and `pytest`, and Alembic's settings (`[tool.alembic]`, pointing it at `migrations/`; there is no `alembic.ini`) | Working |
| `.env.example` | A template listing every setting and secret the project needs, with fake values. Copy it to `.env` and fill it in | Working |
| `.env` | **Your real settings and secrets** (Jira token, database password). Not in Git, and never will be | Working, local only |
| `.gitignore` | Tells Git which files never to save: secrets, Terraform state, caches, generated files | Working |
| `.gitattributes` | Line-ending rules. Stops Windows from adding `\r` characters that break shell scripts on Linux | Working |
| `.vscode/settings.json` | Hides tool caches (`.venv`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`, `__pycache__`, `.terraform`) from VS Code's file tree so the top level stays readable. They still exist on disk and are gitignored | Working |
| `compose.yaml` | The Phase 1–2 stack: Ollama (the AI model), Postgres + pgvector, a one-off migration step, the gateway, and an on-demand `signals` step that writes fake incidents (`make signals`). `make dev` runs it **on the AWS dev server**, not the laptop. No folder-sharing (bind mounts), because those would point at the server's disk; data lives in named volumes there. Never used for staging or prod (the Helm chart is) | Working (`KAV-22`, `signals` in `KAV-23`) |
| `.dockerignore` | What `docker build` may send to the dev server. An allowlist: only `pyproject.toml`, `uv.lock`, `services/` and `migrations/`, so `.env` and Terraform files can't leave the laptop by accident | Working |
| `.gitleaks.toml` | Rules for gitleaks, the scanner that blocks any commit containing a secret | Working |

## `.claude/` and `.github/`

| Path | What it is | Status |
|---|---|---|
| `.claude/settings.json` | Which read-only commands (e.g. `terraform plan`, `kubectl get`) Claude Code may run without asking you each time | Working |
| `.claude/skills/` | Project-specific Claude Code skills, e.g. a `cost-check` skill | Placeholder, no phase set |
| `.github/workflows/ci.yml` | Runs on every pull request and on `main`: lint, tests against a real Postgres, migration checks, a secrets scan of the whole history, Terraform checks, and arm64 image builds scanned by Trivy. Builds, never publishes. Also checks every commit message a PR adds (`KAV-25`). A PR merges only when all of it is green ([ADR-0010](adr/0010-ci-pipeline-and-supply-chain.md), [Lab 06](labs/lab-06-ci-pipeline.md)) | Working (`KAV-24`) |
| `.github/workflows/` (the rest) | `release.yml`, `promote.yml` and `rollback.yml` will move a build through staging to production | Placeholder, Phase 4 |
| `.github/dependabot.yml` | Once a week, opens one pull request per kind of dependency (GitHub Actions, Python, Docker base images, Terraform) that has an update. Each goes through CI like any other change | Working (`KAV-24`) |

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
| `services/collector/` | Gathers raw observations (Kubernetes events, Prometheus metrics, AWS cost data) and saves them as `signal` rows. Read-only | Fake signals working (`KAV-23`); real ones in Phase 3, cost data in Phase 7 |
| `services/collector/kaval_collector/synthetic.py` | Writes fake incidents: the burst of signals one real failure produces (memory kill, wrong-CPU image, cost spike, forgotten disk), in the real payload shapes, always flagged `synthetic: true`. `make signals SCENARIO=oom-crashloop` | Working (`KAV-23`) |
| `services/collector/Dockerfile` | The collector image: arm64, non-root, only the core dependencies (no web framework). Run one-shot by the `signals` service in `compose.yaml` | Working (`KAV-23`) |
| `services/collector/tests/` | Tests for the fake signals: shapes, flags, same seed same content, a run landing whole in the database | Working |
| `services/agent/` | Groups signals into incidents, asks the AI for a diagnosis, and writes a `proposal`. **Read-only credentials; it can only suggest** | Placeholder, Phase 2 |
| `services/executor/` | The **only** component allowed to change the cluster or AWS. Acts only on proposals that passed the policy check and, where required, human approval | Placeholder, Phase 3 |
| `services/gateway/` | The web API (FastAPI) the phone app talks to. Today: `GET /healthz` (passes only when the database is migrated **and** the model is downloaded) and a read-only `/v1` API for signals and incidents, documented at `/docs`. Its rules are in [ADR-0009](adr/0009-gateway-api-conventions.md). `Dockerfile` builds it for arm64 as a non-root user; the same image runs the database migrations | Health check (`KAV-22`) and read-only API (`KAV-23`) working; approve/deny and push in Phase 5 |
| `services/gateway/kaval_gateway/api.py` | The `/v1` routes: list and fetch signals and incidents, newest first, paged with a cursor. Every request's database transaction is read-only, so the database itself refuses writes | Working (`KAV-23`) |
| `services/gateway/kaval_gateway/schemas.py` | The shapes the API returns, kept separate from the database tables so a new column can't leak out by accident | Working (`KAV-23`) |
| `services/gateway/tests/` | Health-check tests, plus API tests against a real database: paging, filters, the incident timeline, error codes, the read-only guard | Working |
| `services/conftest.py` | The shared test fixture `db_session`: each test runs inside a transaction that's rolled back, and tests needing the database skip when none is reachable | Working (`KAV-23`) |

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
| `inference/` | Model-serving files, e.g. an Ollama `Modelfile` with a built-in system prompt. Not needed yet: the model's settings are in `compose.yaml` (context length, one model loaded at a time) and in each request (temperature 0, fixed seed), and keeping them in one place stops them drifting apart | Placeholder, Phase 2 if the agent needs a baked prompt |
| `policy/` | The rules for every proposed action: `auto` (do it), `ask` (a human decides) or `never` (refused). Checked twice, once by the agent and again by the executor. `README.md` explains the design | Design written; rules in Phase 2 |
| `mobile/` | The Android phone app (Expo / React Native): see incidents, read the AI's explanation, tap Approve or Deny | Placeholder, Phase 5 |

---

## Where it runs

### `infra/`: Terraform, the things created in AWS

**Modules** are reusable building blocks. **Environments** assemble modules for one purpose.

| Path | What it is | Status |
|---|---|---|
| `infra/modules/budget/` | Budget alarms at $30 and $35, and a Lambda function that shuts compute down at $38 (raised from $18 / $22 / $24 with the $40 ceiling, ADR-0008). Built **before** anything that can cost money. `lambda/hard_stop.py` is that function. Today it **stops the dev server** (and any other `Project=kaval` server outside a server group), and it scales the Phase 4 group to zero once that exists | Working, applied and fired for real |
| `infra/modules/devbox/` | The development server: a `t4g.medium` with **no open ports** (reached only through AWS Session Manager) that stops itself after an idle hour. `user_data.sh.tftpl` is its first-boot setup: Docker, your SSH key, the idle-stop timer | Working, applied in AWS (Lab 03) |
| `infra/modules/database/` | The production **database server**: its own `t4g.small`, a separate encrypted data volume, a firewall that only lets the app server in, daily snapshots and a snapshot before every stop ([ADR-0008](adr/0008-production-database-on-its-own-server.md)). Staging gets one from the same module | Placeholder, Phase 4 (`KAV-32`) |
| `infra/modules/network/` | The VPC, subnets and firewall rules. No NAT Gateway, which alone would cost $32/month | Placeholder, Phase 4 |
| `infra/modules/node/` | The single cheap `t4g.medium` spot server that runs k3s | Placeholder, Phase 4 |
| `infra/modules/ecr/` | Where container images are stored in AWS | Placeholder, Phase 4 |
| `infra/modules/iam/` | Permissions, including the narrow write access only the executor gets | Placeholder, Phase 4 |
| `infra/modules/eks-lab/` | A real Amazon EKS cluster, created briefly to prove the same software runs there, then destroyed | Placeholder, Phase 8 |
| `infra/envs/prod/` | The production environment. Today it contains only the budget module; the compute modules are written in but commented out | Working (budget only) |
| `infra/envs/prod/main.tf`, `variables.tf`, `outputs.tf` | What to create, its settings, and what it reports back | Working |
| `infra/envs/prod/terraform.tfvars.example` | Template for your real values (`terraform.tfvars` itself is not in Git) | Working |
| `infra/envs/prod/.terraform.lock.hcl` | Pins exact provider versions, so every run uses the same ones | Working |
| `infra/envs/dev/` | Uses the `devbox` module. The Phase 1–3 stack runs here instead of on the laptop ([ADR-0007](adr/0007-develop-on-an-aws-dev-server.md)). About $5/month. `make devbox-up`, `devbox-down`, `devbox-ssh` | Working, applied in AWS (Lab 03) |
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
| `docs/labs/` | **Step-by-step guides**, one per session, detailed enough for a stranger to repeat from zero. Also mirrored to Confluence | Working (Labs 00–07) |
| `docs/learn/` | **Concept pages**, one per phase: *why* things work the way they do, glossary, self-check questions, interview answers. Each is marked "written from theory" until the phase is done, then rewritten from experience | Working (all 11 written) |
| `docs/adr/` | **Architecture Decision Records**: each big decision, why it was made, what was rejected. Numbered, never deleted | Working (0001–0011) |
| `docs/journal/` | **One dated entry per work session**: what was done, what broke, what's still open. The "Open threads" section of the newest entry feeds the dashboard's Blockers panel | Working |
| `docs/architecture/overview.md` | How the system fits together, in words and sketches | Working |
| `docs/architecture/model-shortlist.md` | The local AI models measured on the prod-sized server: memory, speed, output-format results, and which three go to the Phase 2 evals (`make bench` reproduces it) | Working (`KAV-22`) |
| `docs/architecture/architecture.toml` | The system's architecture written as data: every component and connection, and which phase it arrives in. The dashboard draws the three diagrams from this. Update it whenever a component is added, removed or rewired (Definition of Done item 6) | Working |
| `docs/architecture/diagrams/` | Exported diagram images. The live diagrams come from `docs/architecture/architecture.toml` and appear on the dashboard | Placeholder, Phase 9 |
| `docs/runbooks/` | **Troubleshooting guides**, one per failure type. Written for humans, **and** the AI agent reads them when diagnosing incidents | Working (1 runbook); more in Phase 6 |
| `docs/cost/budget-plan.md` | Every expected cost, the $40/month ceiling, and how it's enforced | Working |
| `docs/cost/actuals/` | The real bill, one file per month | Placeholder. Starts with the first real spend, in Phase 4 |
| `docs/releases/` | A change record for every production release, generated automatically: what changed, who approved it, how to roll back | Format in `README.md`; the first record comes in Phase 4 |
| `docs/course/outline.md` | The plan for turning the labs into a course or video series | Working |
| `docs/course/episode-scripts/` | Scripts for each episode | Placeholder, Phase 9 |
| `docs/dashboard.html` | The generated dashboard page. Not in Git; the published copy is the real one | Generated |

### `scripts/`: helper programs, grouped by what they touch

| Path | What it does | Status |
|---|---|---|
| **`scripts/ops/`** | **AWS and the database** | |
| `scripts/ops/cost-report.sh` | Month-to-date AWS spend against the $40 ceiling (`make cost-report`) | Working |
| `scripts/ops/backup.sh` | Nightly database dump to S3 (`make backup`) | Written, not yet run. Needs the production database from Phase 4 |
| `scripts/ops/restore.sh` | Restores a backup into staging (made anonymous first) or, in an emergency, into production | Written, not yet run (Phase 4) |
| `scripts/ops/anonymise.sql` | Strips personal and secret data from a copy before staging gets it | Written, not yet run (Phase 4) |
| **`scripts/tracking/`** | **Jira, Confluence and the dashboard** | |
| `scripts/tracking/dashboard.py` | Builds the progress dashboard from the repo (`make dashboard`) | Working |
| `scripts/tracking/dashboard.toml` | Things the dashboard can't work out by itself: links, cost figures and budget thresholds, per-phase Jira/Confluence links | Working |
| `scripts/tracking/publish-confluence.py` | Publishes the repo's docs to the Confluence space (`make docs-sync` runs both) | Working |
| `scripts/tracking/jira-sync.py` | Lists, creates and moves Jira stories; ticks their acceptance criteria; records UAT verdicts (`uat pass\|fail`, [ADR-0012](adr/0012-user-acceptance-testing.md)); fills the Service field from the code each story changed (`backfill-service`). `make jira EPIC=KAV-6` | Working |
| `scripts/tracking/atlassian.py` | The Jira/Confluence login code, shared by the two scripts above | Working |
| **`scripts/dev/`** | **Setting up your own machine** | |
| `scripts/tracking/jira_adf.py` | Builds and reads Jira story descriptions (Context, Acceptance Criteria, UAT scenarios) and maps code folders to the Service field; no network, so it's unit-tested in `test_jira_adf.py` | Working (`KAV-34`) |
| `scripts/dev/install-hooks.sh` | Installs the two Git hooks: `pre-commit` (blocks secrets) and `commit-msg` (the commit convention) | Working |
| `scripts/dev/check_commits.py` | The commit-convention checker, used by the `commit-msg` hook and by CI on every PR; tests in `test_check_commits.py` ([ADR-0011](adr/0011-commit-convention-and-jira-link.md), [Lab 07](labs/lab-07-commits-and-jira-link.md)) | Working (`KAV-25`) |
| `scripts/dev/new-lab.sh` | Creates a new lab document and journal entry from a template (`make lab NAME=...`) | Working |
| `scripts/dev/bench_models.py` | Measures the local model shortlist on the dev server: memory, load time, time to first token, speed, JSON validity (`make bench`). Results in `docs/architecture/model-shortlist.md` | Working (`KAV-22`) |

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
| `infra/envs/*/terraform.tfvars` | Your real email, SSH public key and settings |
| `infra/envs/*/terraform.tfstate` | Terraform's record of what it created in AWS (prod and dev each have one); it can contain account details. **Only copy is on this laptop**, so it moves to S3 later |
| `~/.ssh/kaval-devbox` | The dev server's SSH private key. It lives in your home folder, outside the project entirely |
| `.venv/` | Installed Python packages. Rebuild with `make sync` (exactly what `uv.lock` pins) |
| `docs/dashboard.html` | Regenerated every time |
| Caches (`__pycache__`, `.mypy_cache`, `.terraform/`...) | Rebuilt automatically |

---

When a folder is added, moved or starts being used, update this guide in the same change.
