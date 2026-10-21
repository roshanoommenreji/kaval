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
| `Makefile` | Short commands for everything (`make help` lists them): `make dev`, `make signals`, `make correlate`, `make test`, `make lint`, `make sync`, `make lock`, `make migrate`, `make docs-sync`, `make jira`, `make plan`... | Working. Some targets wait on later phases (see below) |
| `uv.lock` | The **exact** version and file hashes of every Python package (46), generated from `pyproject.toml` by `make lock`. CI, the laptop (`make sync`) and both images install from it, so all three run the same code. Never edited by hand | Working (`KAV-24`) |
| `pyproject.toml` | Python project settings: dependencies, the rules for `ruff`, `mypy` and `pytest`, and Alembic's settings (`[tool.alembic]`, pointing it at `migrations/`; there is no `alembic.ini`) | Working |
| `.env.example` | A template listing every setting and secret the project needs, with fake values. Copy it to `.env` and fill it in | Working |
| `.env` | **Your real settings and secrets** (Jira token, database password). Not in Git, and never will be | Working, local only |
| `.gitignore` | Tells Git which files never to save: secrets, Terraform state, caches, generated files | Working |
| `.gitattributes` | Line-ending rules. Stops Windows from adding `\r` characters that break shell scripts on Linux | Working |
| `.vscode/settings.json` | Hides tool caches (`.venv`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`, `__pycache__`, `.terraform`) from VS Code's file tree so the top level stays readable. They still exist on disk and are gitignored | Working |
| `compose.yaml` | The Phase 1–2 stack: Ollama (the AI model), Postgres + pgvector, a one-off migration step, the gateway, and on-demand `signals` and `agent` steps that write fake incidents (`make signals`) and group them into incidents (`make correlate`). `make dev` runs it **on the AWS dev server**, not the laptop. No folder-sharing (bind mounts), because those would point at the server's disk; data lives in named volumes there. Never used for staging or prod (the Helm chart is) | Working (`KAV-22`, `signals` in `KAV-23`, `agent` in `KAV-39`) |
| `.dockerignore` | What `docker build` may send to the dev server. An allowlist: only `pyproject.toml`, `uv.lock`, `services/` and `migrations/`, so `.env` and Terraform files can't leave the laptop by accident | Working |
| `.gitleaks.toml` | Rules for gitleaks, the scanner that blocks any commit containing a secret | Working |

## `.claude/` and `.github/`

| Path | What it is | Status |
|---|---|---|
| `.claude/settings.json` | Which read-only commands (e.g. `terraform plan`, `kubectl get`) Claude Code may run without asking you each time | Working |
| `.claude/skills/` | Project-specific Claude Code skills, e.g. a `cost-check` skill | Placeholder, no phase set |
| `.github/workflows/ci.yml` | Runs on every pull request and on `main`: lint, tests against a real Postgres, migration checks, a secrets scan of the whole history, Terraform checks, the Helm chart linted/rendered/schema-validated (kubeconform), and arm64 image builds scanned by Trivy. Builds, never publishes. Also checks every commit message a PR adds (`KAV-25`). A PR merges only when all of it is green — enforced by branch protection since the repo went public (`KAV-45`) ([ADR-0010](adr/0010-ci-pipeline-and-supply-chain.md), [Lab 06](labs/lab-06-ci-pipeline.md)) | Working (`KAV-24`, `KAV-46`) |
| `.github/workflows/release.yml` | Stage one of the release: on a merged change to a service (or by hand) it builds the five images once on arm64, scans them with Trivy, pushes them to ECR as `sha-<short>` through a push-only OIDC role, tags any version that has no tag yet (`<svc>-vX.Y.Z`, `vX.Y.Z`), and opens a pull request that points staging at the tag. The publishing job can reach AWS but not the repo; the proposing job can write the repo but not reach AWS ([ADR-0030](adr/0030-ci-publishes-images-by-oidc-and-staging-is-pinned-by-pull-request.md), [Lab 32](labs/lab-32-release-publish-stage.md)) | Built (`KAV-61`); the smoke test and passed-staging record are `make staging-smoke` (`KAV-62`); version tags are the next stage |
| `.github/workflows/promote.yml` | The promotion gate, run by hand with a tag: the `gate` job (read-only AWS and Jira) answers the four questions, then `propose` opens the pull request that points prod at the tag, with the generated change record (`docs/releases/`) in it. Merging it is the go/no-go. The only workflow that writes `deploy/gitops/prod/` | Working (`KAV-63`) |
| `.github/trivy-ignore.yaml` | The only vulnerabilities the image scans overlook: per-CVE, scoped to one file, each with a reason and an end date (currently one Go CVE in the bundled `opa`, until 2026-11-04; the first three ended when OPA v1.21.3 was pinned on 2026-10-21, ADR-0033) | Working (`KAV-64`) |
| `.github/workflows/release-prepare.yml` | Run by hand from `main`: works out the next versions from the commits and opens `chore(release): Kaval X.Y.Z`, which writes them into `__version__`, `pyproject.toml` and `uv.lock`. Merging it starts `release.yml`, which tags them. Reads Git, touches no AWS or Jira | Built (`KAV-67`) |
| `.github/workflows/backup-watch.yml` | Daily at 07:30 UTC: is the nightly dump being taken (only for nights the app server was running; a parked production owes none), is the newest dump a plausible size, has a restore drill been recorded in the last 35 days? The `check` job reads AWS (read-only) and cannot write; the `tell` job can open one GitHub issue and has no AWS ([ADR-0049](adr/0049-backups-watched-and-restores-drilled.md), [runbook](runbooks/backup-missing.md)) | Built (`KAV-82`); needs the Terraform apply and two repository variables before its first run |
| `.github/workflows/rollback.yml` | The emergency exit, run by hand with a tag and a reason: the `gate` job (read-only AWS, never Jira) checks the tag was run by prod before or passed staging, is older than prod, still has its four images, and does not cross a database migration unless told it was undone; `propose` opens the pull request that points prod back and marks the undone release's change record `Rolled back: yes`. Merging it is the go/no-go ([ADR-0034](adr/0034-rollback-workflow-and-what-a-rollback-may-go-back-to.md), [Lab 35](labs/lab-35-rollback-workflow.md), [runbook](runbooks/rollback-prod.md)) | Working (`KAV-66`, `KAV-71`) |
| `.github/dependabot.yml` | Once a week, opens one pull request per kind of dependency (GitHub Actions, Python, Docker base images for all five services, Terraform) that has an update. Each goes through CI like any other change. `kubernetes` is no longer held back (`KAV-70`) | Working (`KAV-24`, `KAV-69`, `KAV-70`) |

---

## The product

### `services/`: the Python programs

Each subfolder becomes one container. The most important design rule lives here: **the part
that thinks (`agent`) cannot change anything; the part that changes things (`executor`) does not
think.**

| Path | What it will do | Status |
|---|---|---|
| `services/shared/` | Code every service shares. Today that's the database layer | Working |
| `services/shared/kaval_shared/models.py` | The database tables that record each incident from start to finish: `signal → incident → proposal → action → decision → execution → outcome`, plus `notification` (what a person was told about Kaval's own health, `KAV-78`). Rows are added, never edited, so the tables are the audit trail | Working |
| `services/shared/kaval_shared/metrics.py` | The health page every service shows on port 9100: when its last pass finished, how often one should, how many failed, and its real memory. Nothing from the data is ever on it (a test enforces that) | Working (`KAV-78`, [ADR-0042](adr/0042-watching-the-watcher.md)) |
| `services/shared/kaval_shared/health.py` | The rule that a cause starting `kaval_` is Kaval's own health, and (`KAV-81`) `disk_pressure` and `latency_high`, are for a person and never for a proposed fix; repeated in `policy/policy.rego` and kept equal by a test | Working (`KAV-78`, `KAV-81`) |
| `services/gateway/kaval_gateway/health_alerts.py` | Posts "Kaval's own health" to Slack straight from the signals (not via the correlator), once per fault per 30 minutes, recorded in `notification` | Working, tested; the Slack hop is seen live only in prod (`KAV-78`, `KAV-83`) |
| `services/shared/kaval_shared/db.py` | Opens the database connection from the `POSTGRES_*` settings in `.env` | Working |
| `services/shared/tests/` | Tests for the tables: one checks their shape, one writes a full incident through all seven, one runs the staging scrub (`anonymise.sql`) over planted secrets and fails when a new text column has no decision | Working |
| `services/collector/` | Gathers raw observations (Kubernetes events, Prometheus metrics, AWS cost data) and saves them as `signal` rows. Read-only | Fake signals (`KAV-23`), real Kubernetes events (`KAV-48`) and Prometheus memory-near-limit (`KAV-75`) working; cost data in Phase 6 |
| `services/collector/kaval_collector/synthetic.py` | Writes fake incidents: the burst of signals one real failure produces (memory kill, wrong-CPU image, cost spike, forgotten disk), in the real payload shapes, always flagged `synthetic: true`. `make signals SCENARIO=oom-crashloop` | Working (`KAV-23`) |
| `services/collector/kaval_collector/k8s_events.py` | Polls real Kubernetes events in one namespace, de-duplicated by the event's own `count`, writes the identical row shape `synthetic.py` uses. `make watch-events`, or deployed continuously via the Helm chart | Working, scoped RBAC — Phase 3, `KAV-48` |
| `services/collector/kaval_collector/probe.py` | The slowness check (`KAV-81`, ADR-0047): one timed request per configured service each pass of the poller; slow = over 1 s or no answer in 5 s, three passes in a row; a refused connection is down, not slow; targets from settings only, no redirects. Runs inside `prometheus.py`'s loop, not as a program of its own | Working, proven live on staging |
| `services/collector/kaval_collector/waste.py` | The waste scan's cluster half (`KAV-94`, [ADR-0062](adr/0062-waste-is-found-daily-by-a-read-only-scan-and-reported-to-a-person.md)): read by the cost reader's pod (`--waste-handoff`), it treats the ConfigMap the server copied the scan into as untrusted and rebuilds every finding from a whitelist (known kind, target made from the finding's own AWS id, only named fields, each checked), writes one signal per finding per daily scan, and counts what it refused. A scan that is missing, failed, stale or unreadable sets a health number; "not run yet" is waiting, not failing | Built and tested; switched on in staging after its build is pinned |
| `services/collector/kaval_collector/removal.py` | The removal worker's answers, read by the cost reader's pod (`KAV-95`, [ADR-0063](adr/0063-waste-is-removed-in-steps-by-a-separate-worker-and-only-with-approval.md)): every answer rebuilt from a whitelist of fixed words and ids, written once as a `removal_done` record or a `removal_failed` incident, and the age of the oldest unanswered order as a number for the alarm | Working |
| `services/collector/kaval_collector/cost_explorer.py` | The cost reader's cluster half (`KAV-92`, [ADR-0060](adr/0060-the-cost-reader-the-server-asks-aws-and-the-cluster-never-holds-the-login.md)): reads the AWS bill the server's daily job leaves in one ConfigMap and writes `daily_cost` signals, one per service per settled day (a day at least two old), once, nothing for zero. Holds no AWS login. Anything it cannot trust (no ConfigMap, a failed or capped pull, an answer over 36 hours old, a bill that does not parse) sets a health number the alarm reads. Tested against a recorded real bill in `tests/fixtures/` | Built, tested; staging proof in Lab 57 |
| `services/collector/kaval_collector/prometheus.py` | Asks Prometheus which containers are above 90% of their memory limit and writes `container_memory_near_limit` signals in the shape the synthetic scenarios use, renewing every 5 minutes while a container stays high. Standard-library HTTP; reports a Prometheus that is down on every pass and keeps going ([ADR-0040](adr/0040-prometheus-as-a-polled-signal-source.md)). `make watch-metrics`, or deployed via the chart | Working — Phase 3, `KAV-75`, Lab 43 |
| `services/collector/Dockerfile` | The collector image: arm64, non-root, core deps plus its own `collector` extra (the Kubernetes client, `KAV-48`). One-shot via `signals`/`watch-events` in `compose.yaml`; the Helm chart overrides its entrypoint for the deployed watcher | Working (`KAV-23`, `KAV-48`) |
| `services/collector/tests/` | Tests for the fake signals (shapes, flags, reproducibility), the real event watcher (reason→kind mapping, count-based de-duplication) and the Prometheus poller (threshold, no-limit, renew-while-it-persists) | Working |
| `services/agent/` | Groups signals into incidents, builds the context for a diagnosis, asks the AI for one, and writes a `proposal`. **Read-only credentials; it can only suggest** | Correlation, context builder, schema-enforced diagnosis and the policy engine working (`KAV-39`–`KAV-42`) |
| `services/agent/kaval_agent/correlate.py` | Groups the signals not yet in an incident into `incident` rows, by what's broken (the workload or AWS resource) and why (the most specific cause). Names each with a fingerprint like `oom_killed:k8s:kaval-demo/checkout`, and closes it once quiet. Rules, not AI ([ADR-0014](adr/0014-signal-correlation-and-incident-fingerprints.md)). `make correlate` | Working (`KAV-39`) |
| `services/agent/Dockerfile` | The agent image: arm64, non-root, only the core dependencies. It has no Kubernetes or AWS library to change anything with. Run one-shot by the `agent` service in `compose.yaml` | Working (`KAV-39`) |
| `services/agent/kaval_agent/context.py` | Assembles what a diagnosis needs: matching runbook sections, similar past incidents with their outcomes, recent changes if supplied. Read-only, no writes ([ADR-0015](adr/0015-context-builder-retrieval-design.md)). `make context INCIDENT=<uuid>` | Working (`KAV-40`) |
| `services/agent/kaval_agent/embeddings.py` | Calls Ollama's `/api/embed` (all-minilm, 384-dim), validates every vector's shape before it's trusted | Working (`KAV-40`) |
| `services/agent/kaval_agent/runbooks.py` · `index_runbooks.py` | Splits a runbook into its `## ` sections and syncs them into `runbook_chunk`, re-embedding only what changed. A laptop tool (`make index-runbooks`) and, since `KAV-87`, run by the diagnosis worker at start from the runbooks copied into the agent image | Working (`KAV-40`, `KAV-87`) |
| `services/agent/kaval_agent/recent_changes.py` | Maps a workload to its service folder and reads `git log`; laptop-only, and honestly a proxy until Phase 3's real deploy signals exist | Working (`KAV-40`) |
| `services/agent/kaval_agent/schema.py` | `Diagnosis`/`DiagnosisAction`: the Pydantic shape a model reply must satisfy. No SQLAlchemy or httpx import — importable by anything (eval harness, tests) that needs the shape without a live Ollama | Working (`KAV-41`) |
| `services/agent/kaval_agent/fixes.py` | The one list of fixes Kaval can carry out and which incident causes each answers; builds the section the model reads last and narrows the reply format to one incident (`KAV-84`, [ADR-0054](adr/0054-telling-the-model-which-fixes-exist.md)). Pure: no database, no httpx, no executor import. A test fails if it and the executor's `SUPPORTED_ACTIONS` differ | Working (`KAV-84`) |
| `services/agent/kaval_agent/diagnoser.py` | The diagnosis worker the cluster runs (`python -m kaval_agent.diagnoser --every 30`): picks the oldest open incident with no proposal that is not for a person, asks the model, files the proposal; one diagnosis per incident, 6 an hour, a pause after a model failure, its own metrics and alerts. A separate Deployment from the correlator so a slow model cannot stop incidents being found (`KAV-87`, [ADR-0055](adr/0055-the-model-runs-in-the-cluster-and-diagnoses-new-incidents.md)) | Built (`KAV-87`, Lab 52) |
| `services/agent/kaval_agent/removal.py` | The removal planner (`KAV-95`, [ADR-0063](adr/0063-waste-is-removed-in-steps-by-a-separate-worker-and-only-with-approval.md)): files the proposals that remove a loose disk (a backup-and-mark step, then a delete after the grace period) and an old backup copy, **by code, never by the model**; never asks a step twice, never about a stale fact, never more than five waiting; run inside the diagnoser's loop, with no AWS or cluster login. Tests: `services/agent/tests/test_removal_planner.py`, and the whole ladder end to end in `test_removal_ladder.py` | Working |
| `services/agent/kaval_agent/diagnose.py` | Calls the local model schema-constrained (Ollama `format`), re-validates with Pydantic anyway, one corrective retry, writes a `proposal` + `action` rows or nothing at all ([ADR-0016](adr/0016-json-schema-enforced-proposal-output.md)). Each action's `policy_class` comes from `kaval_agent.policy`, not the model. Since `KAV-84` the message ends with the fix for the incident's cause and the reply format allows only that fix. `make diagnose INCIDENT=<uuid>` | Working (`KAV-41`, `KAV-84`) |
| `services/agent/kaval_agent/evidence.py` | Limits the stored confidence by the evidence: one observation at most 0.5, several of one kind at most 0.7, two kinds the model's number. Only lowers; called in `write_proposal` ([ADR-0056](adr/0056-the-stored-confidence-follows-the-evidence.md), `KAV-88`) | Working |
| `services/agent/kaval_agent/policy.py` | Classifies one action as `auto`/`ask`/`never` by shelling out to `opa eval` against `policy/policy.rego`. Falls back to `ask` if OPA can't be evaluated at all ([ADR-0017](adr/0017-opa-policy-engine-and-earned-autonomy.md)). `make policy-check TYPE=... BLAST_RADIUS=... CONFIDENCE=...` | Working (`KAV-42`) |
| `services/agent/kaval_agent/escalate.py` | Escalates one incident to Claude Haiku 4.5 on Bedrock when the local model fails, the context has no retrieved evidence, or confidence is low — same schema-validate-regardless contract as `diagnose.py`, via a forced tool call ([ADR-0019](adr/0019-bedrock-escalation-and-the-mantle-client-rejection.md)). Uses the classic `AnthropicBedrock` client, not the newer Mantle client, which 404'd for this account. `make escalate INCIDENT=<uuid>`, or `make diagnose INCIDENT=<uuid> ESCALATE=1` | Built and unit-tested; live escalation blocked on an AWS Marketplace payment issue (`KAV-44`) |
| `services/agent/tests/` | Tests for correlation, context assembly, embeddings (mocked, never a real Ollama), runbook syncing, the diagnosis schema, the model call (mocked), the policy classifier (a real `opa`, never mocked), and the Bedrock escalation call (mocked, never a real Bedrock request) | Working (`KAV-39`–`KAV-42`, `KAV-44`) |
| `services/executor/` | The **only** component allowed to change the cluster or AWS. Consumes `auto`-classified or human-approved actions, re-checks policy itself before acting, redacts `stdout` at write time | Working (`restart_pod`; and since `KAV-79` `increase_memory_limit`, which doubles a memory limit up to 512 MiB and refuses rather than guesses), scoped RBAC in `deploy/charts/kaval` — Phase 3, `KAV-47`; since `KAV-77` also records whether each fix held (`verify.py`) and reports time to restore (`mttr.py`, `make mttr`); `chaos.py` is the game-day failure maker (a different command and identity from the executor itself, `KAV-79`, [ADR-0044](adr/0044-chaos-with-our-own-jobs-and-a-bounded-memory-fix.md)) |
| `services/backup/` | A utility image, not an application service: `postgresql-client` + `awscli`, runs `scripts/ops/backup.sh`/`db-roles.sql` as Helm CronJob/hook containers | Built (`KAV-32`); its chart CronJob is disabled and unused: the nightly dump is taken by a timer on the app node instead (`KAV-74`, ADR-0038) |
| `services/gateway/` | The web API (FastAPI) the phone app talks to. Today: `GET /healthz` (passes only when the database is migrated **and** the model is downloaded) and a read-only `/v1` API for signals and incidents, documented at `/docs`. Its rules are in [ADR-0009](adr/0009-gateway-api-conventions.md). `Dockerfile` builds it for arm64 as a non-root user; the same image runs the database migrations | Health check (`KAV-22`) and read-only API (`KAV-23`) working; approve/deny and push in Phase 5 |
| `services/gateway/kaval_gateway/api.py` | The `/v1` routes: list and fetch signals and incidents, newest first, paged with a cursor. Every request's database transaction is read-only, so the database itself refuses writes | Working (`KAV-23`) |
| `services/gateway/kaval_gateway/schemas.py` | The shapes the API returns, kept separate from the database tables so a new column can't leak out by accident | Working (`KAV-23`) |
| `services/gateway/kaval_gateway/decisions.py` | `record_decision()` — the one write every approval surface shares (`api.decide()` and Slack ChatOps both call it) | Working (`KAV-55`) |
| `services/gateway/kaval_gateway/approvals.py` | Approvals that nobody answers: a reminder at 15 minutes, a louder message at 30, an expiry at 4 hours; `is_expired` is what `decisions.record_decision` uses to refuse a late click. Settings `APPROVAL_*_MINUTES` (`KAV-82`, ADR-0048) | Working, tested; live on staging, Slack delivery seen in prod only (`KAV-83`) |
| `services/gateway/kaval_gateway/slack_chatops.py` | Slack ChatOps (ADR-0026): an outbound Socket Mode connection, no inbound endpoint ever. No-op unless `SLACK_BOT_TOKEN`/`SLACK_APP_TOKEN`/`SLACK_CHANNEL_ID` are set | Working, live-verified on the dev server's k3d cluster (`KAV-55`, Lab 21) |
| `services/gateway/tests/` | Health-check tests, plus API tests against a real database: paging, filters, the incident timeline, error codes, the read-only guard | Working |
| `services/conftest.py` | The shared test fixture `db_session`: each test runs inside a transaction that's rolled back, and tests needing the database skip when none is reachable | Working (`KAV-23`) |

### `migrations/`: how the database gets its tables

| Path | What it is | Status |
|---|---|---|
| `migrations/versions/` | One file per change to the database structure: the seven spine tables, then the correlation index (`KAV-39`), then `runbook_chunk` and the pgvector extension (`KAV-40`), `action.slack_notified_at` (`KAV-55`) and the `notification` table (`KAV-78`) | Working |
| `migrations/versions/d4f7a2c9e815_blast_radius_resource.py` | Adds the blast radius `resource` (one disk or backup copy) for removal (`KAV-95`); only adds a value | Working |
| `migrations/env.py` | Connects Alembic to the database and to `models.py` | Working |
| `migrations/script.py.mako` | Template for new migration files | Working |

Run `make migrate` to bring a database up to date.

### `inference/`, `policy/`, `mobile/`

| Path | What it will do | Status |
|---|---|---|
| `inference/` | Model-serving files, e.g. an Ollama `Modelfile` with a built-in system prompt. Not needed yet: the model's settings are in `compose.yaml` (context length, one model loaded at a time) and in each request (temperature 0, fixed seed), and keeping them in one place stops them drifting apart | Placeholder, Phase 2 if the agent needs a baked prompt |
| `policy/` | The rules for every proposed action: `auto` (do it), `ask` (a human decides) or `never` (refused), in Rego, tested with `opa test policy/ -v`. `promotions.json` is the empty-by-default list that has to be explicitly populated (with an ADR citing evidence) before anything can reach `auto`. `README.md` explains the design; the agent side is built, the executor's second check is Phase 3 | Agent-side working (`KAV-42`, [ADR-0017](adr/0017-opa-policy-engine-and-earned-autonomy.md)) |
| `mobile/` | The Android phone app (Expo / React Native): see incidents, read the AI's explanation, tap Approve or Deny | Placeholder, Phase 9 — deferred (ADR-0026); Slack ChatOps (`KAV-55`, Phase 4) is the real approval surface until/unless this is built |

---

## Where it runs

### `infra/`: Terraform, the things created in AWS

**Modules** are reusable building blocks. **Environments** assemble modules for one purpose.

| Path | What it is | Status |
|---|---|---|
| `infra/modules/budget/` | Budget alarms at $42 and $46, and a Lambda function that shuts compute down at $48 (raised from $18 / $22 / $24 with the $40 ceiling, ADR-0008, then from $30 / $35 / $38 with the $50 ceiling, ADR-0028). Built **before** anything that can cost money. `lambda/hard_stop.py` is that function. It **stops the dev server** (and any other `Project=kaval` server outside a server group) and, since `KAV-50`, scales the real prod Auto Scaling Group (and, since `KAV-96`, staging's by name, with 12 tests in `test_hard_stop.py`) to zero — armed (not dry-run) and proven live with a manual test invocation | Working, applied and fired for real |
| `infra/modules/database/monitoring.tf` · `db-monitoring.sh` | The database server's monitoring and patching, applied by SSM State Manager and not boot-time user_data (the server ignores that after creation): a Command document holding the script, an association that runs it on creation, daily and whenever the stopped server returns, the monitoring role's generated password (readable only by this server), and a weekly no-reboot `AWS-RunPatchBaseline`. The script creates the read-only `kaval_monitor` role, runs `node_exporter` and `postgres_exporter` (64 MiB each, private address only), turns on connection logging and caps Docker's log size ([ADR-0050](adr/0050-monitoring-and-patching-the-database-server.md)) | Written, validated; not yet run on a server (`KAV-82`) |
| `infra/modules/backup-watch/` | The AWS role the daily backup check borrows: trusted by OIDC for this repository on `main` only, allowed only to list the dump folder of the backup bucket and read the app server's launch history; no `GetObject`, no writes ([ADR-0049](adr/0049-backups-watched-and-restores-drilled.md)) | Written, validated; not yet applied (`KAV-82`) |
| `infra/modules/cost-reader/` | The login the server's daily cost pull borrows: one permission, `ce:GetCostAndUsage`, trusted for that environment's server role only, and the server's right to borrow it and no other role. IAM only, no cost (`KAV-92`, ADR-0060) | Built, validated; applied to staging for the proof |
| `infra/modules/still-alive/` | The "still alive" alarm: one CloudWatch alarm that e-mails (through an SNS topic) when the app-server group wants a server and the node's two-minute datapoints have stopped for 25 minutes. Reads the group's own size so a parked production is silent (`KAV-83`, ADR-0053). About $0.72 a month at most | Built; applied to staging for the proof, to production in Part 3 |
| `infra/modules/ci-publish/` | The AWS role GitHub Actions borrows to publish images: trusted by OIDC for this repository on `main` only, allowed only to push to the Kaval ECR repositories. It looks up the account's existing GitHub OIDC provider (owned by another project) and never manages it ([ADR-0030](adr/0030-ci-publishes-images-by-oidc-and-staging-is-pinned-by-pull-request.md)) | Working, applied to prod (`KAV-61`) |
| `infra/modules/waste-scan/` | The daily waste scan (`KAV-94`, [ADR-0062](adr/0062-waste-is-found-daily-by-a-read-only-scan-and-reported-to-a-person.md)): a Lambda on its own 03:15 UTC schedule with a **read-only** role (four `ec2:Describe*` calls and the right to read and write one parameter, `/kaval/<env>/waste-scan`; no verb that changes the account, and a test fails if one appears). `lambda/waste_scan.py` holds one pure `scan()` that finds disks and addresses attached to nothing on two scans in a row, backup copies past their retention (never the newest of a disk), staging/dev servers running over 12 hours (never production) and Kaval resources missing their labels; it writes only ids, sizes, dates and fixed words | Built and tested (`KAV-94`); staging live proof in Lab 59, production at its bring-up |
| `infra/modules/waste-removal/` | The removal worker (`KAV-95`, [ADR-0063](adr/0063-waste-is-removed-in-steps-by-a-separate-worker-and-only-with-approval.md)): a Lambda with its **own** narrow role (back up and label a Kaval disk, delete a *marked* disk or a Kaval backup copy, each fenced to `Project=kaval`, this environment and not Terraform's; IAM itself refuses to delete an unmarked disk), woken by a change to the orders parameter and by a 6-hourly safety run, answering into a second parameter. `lambda/waste_removal.py` holds one pure `decide()` that re-reads AWS and refuses on any doubt. Its tests fail if the role names any other verb | Built; applied to staging at the live proof |
| `infra/modules/idle-stop/` | A Lambda on a 15-minute schedule that **parks** an on-demand environment: when no Session Manager session or Run Command has touched its servers for four hours it scales the node's Auto Scaling Group to zero and stops the database server after a snapshot. Stops, never destroys, and its IAM is scoped to the `Env=staging` tag ([ADR-0029](adr/0029-staging-parks-itself-when-idle.md)). `lambda/idle_stop.py` holds a pure, unit-tested `decide()` | Working, proven live (`KAV-59`, Lab 30) |
| `infra/modules/devbox/` | The development server: a `t4g.medium` with **no open ports** (reached only through AWS Session Manager) that stops itself after an idle hour. `user_data.sh.tftpl` is its first-boot setup: Docker, `k3d`/`kubectl`/`helm` (checksum-verified, `KAV-46`), your SSH key, the idle-stop timer | Working, applied in AWS (Lab 03) |
| `infra/modules/database/` | The production **database server**: its own `t4g.small`, a separate encrypted data volume, a firewall that only lets the app server in, TLS, per-service Postgres roles, daily DLM snapshots ([ADR-0008](adr/0008-production-database-on-its-own-server.md)). Staging gets one from the same module | Built and live (`KAV-32`) |
| `infra/modules/backups/` | The S3 bucket the nightly `pg_dump` lands in, plus the free S3 gateway VPC endpoint so it never crosses a metered path | Built and live (`KAV-32`) |
| `infra/modules/network/` | The VPC, subnets and firewall rules. No NAT Gateway, which alone would cost $32/month | Working, applied in AWS (`KAV-50`/`KAV-51`, Lab 19/20) — one subnet per AZ, so the ASG can launch wherever capacity exists |
| `infra/modules/node/` | The single `t4g.medium` server that runs k3s, in a size-1 Auto Scaling Group so a replaced instance comes back automatically. On-Demand by default since 2026-10-08 ([ADR-0028](adr/0028-prod-app-node-on-demand-and-ceiling-50.md)); `spot = true` restores the ~$8.55/month saving. Also installs the nightly database-dump timer when given a `backup_bucket_name` ([ADR-0038](adr/0038-nightly-dump-is-taken-by-the-app-node.md), `KAV-74`), and, for staging only (`seed_database`), the script that fills an empty database from the cleaned copy before any service starts ([ADR-0039](adr/0039-staging-is-filled-from-a-copy-cleaned-on-the-laptop.md), `KAV-73`) | Working, applied in AWS (`KAV-50`/`KAV-51`, Lab 19/20) — `k3s` is live, `Ready`, and Flux reconciles it from Git on every boot |
| `infra/modules/ecr/` | Where container images are stored in AWS — one repo per image (gateway, agent, executor, collector, backup), immutable tags. Keeps only the newest 15 tagged images per repository and expires untagged ones after a week ([ADR-0036](adr/0036-ecr-keeps-the-newest-fifteen-tagged-images.md)) | Working, applied in AWS (`KAV-50`, Lab 19); retention rule `KAV-68` (Lab 37) |
| `infra/modules/iam/` | The node's own AWS identity — SSM management, scoped ECR pulls, and (since `KAV-32`) scoped S3 access to the backup bucket. **Not** a per-service agent/executor AWS role: that split already exists at the Kubernetes RBAC layer (`KAV-47`); see [ADR-0024](adr/0024-prod-landing-network-ecr-iam-node.md) | Working, applied in AWS (`KAV-50`, Lab 19) |
| `infra/modules/eks-lab/` | A real Amazon EKS cluster, created briefly to prove the same software runs there, then destroyed | Placeholder, Phase 7 |
| `infra/envs/prod/` | The production environment: the budget module (Phase 0), the network/ECR/IAM/node landing (`KAV-50`), Flux (`KAV-51`), and the database server + backups (`KAV-32`, live). The Cloudflare Tunnel is deferred with mobile (ADR-0026) | Working |
| `infra/envs/prod/main.tf`, `variables.tf`, `outputs.tf` | What to create, its settings, and what it reports back | Working |
| `infra/envs/prod/terraform.tfvars.example` | Template for your real values (`terraform.tfvars` itself is not in Git) | Working |
| `infra/envs/prod/node.auto.tfvars` | Written by `make down`, removed by `make up`. Persists the app node's paused state (`app_node_desired_capacity = 0`) as a file Terraform auto-loads, instead of a one-off `-var` flag an unrelated `terraform apply` could forget to repeat (`KAV-32`, Lab 28). Not in Git — it's live operational state, not configuration. | Working |
| `infra/envs/prod/.terraform.lock.hcl` | Pins exact provider versions, so every run uses the same ones | Working |
| `infra/envs/dev/` | Uses the `devbox` module. The Phase 1–3 stack runs here instead of on the laptop ([ADR-0007](adr/0007-develop-on-an-aws-dev-server.md)). About $5/month. `make devbox-up`, `devbox-down`, `devbox-ssh` | Working, applied in AWS (Lab 03) |
| `infra/envs/staging/` | A second, temporary copy of production for testing each release: its own VPC, On-Demand node (own k3s and Flux), database server and backups bucket, built from the same modules as prod with `name_prefix = "kaval-staging"`. Shares prod's ECR (it only pulls). Created on demand by `make staging-up`, parked by its own idle-stop Lambda after four idle hours, destroyed by `make staging-down` (about $0.045/hour while up; Labs 29 and 30). Seeding from a sanitised prod snapshot and the release workflows are separate, unbuilt lines | Working, applied, parked, resumed and destroyed live (`KAV-57`, `KAV-59`) |
| `deploy/gitops/staging/` | The five Flux manifests staging's own cluster follows (namespace, Git source, Helm release, kustomization, sync). Same shape as `deploy/gitops/prod/`; the node's bootstrap script downloads them from `main` at boot, so they must be merged before the first apply | Working (`KAV-57`) |
| `infra/envs/state/` | The bucket that holds Terraform's record of prod, staging and dev (`KAV-86`): versioned, encrypted, TLS-only, locked by S3. A folder of its own so `make staging-down` can never destroy it. Its own record is inside it too |
| `infra/envs/lab-eks/` | Uses the `eks-lab` module | Placeholder, Phase 7 |

### `deploy/`: getting the software onto Kubernetes

| Path | What it will do | Status |
|---|---|---|
| `deploy/charts/kaval/` | One Helm chart. Deploys the gateway, the agent's correlate loop, the scoped-RBAC executor, where switched on (`ollama.enabled`, `diagnoser.enabled`: staging) the language model as its own pod and the diagnosis worker with its alert rules `files/kaval-diagnoser.rules.yaml` (`KAV-87`, [ADR-0055](adr/0055-the-model-runs-in-the-cluster-and-diagnoses-new-incidents.md)), the real-event collector and, where switched on (`costReader.enabled`: nowhere until staging's follow-up) the cost reader pod with its alert rules `files/kaval-cost.rules.yaml` (`KAV-92`, ADR-0060), where switched on (`prometheus.enabled`: local and staging), Prometheus with its poller (`KAV-75`, [ADR-0040](adr/0040-prometheus-as-a-polled-signal-source.md)), a headless Service per service for its health page, and the alert rules in `files/kaval-alerts.rules.yaml` with their promtool tests in `tests/` (`KAV-78`, [ADR-0042](adr/0042-watching-the-watcher.md)) to a real cluster ([ADR-0020](adr/0020-the-helm-chart-and-the-local-k3d-environment.md), [ADR-0021](adr/0021-the-executor-scoped-rbac-and-the-approval-write-path.md), [ADR-0022](adr/0022-real-kubernetes-events-as-signals.md)). Postgres is now optional (`postgres.enabled`, `KAV-32`) — local/dev deploy it in-cluster; prod reads an out-of-band Secret per service, pointed at the standalone database server instead. `helm lint`/`helm template` run through kubeconform in CI, on every environment that has a values file | Working — `local` (`KAV-46`–`48`, Labs 15–17); prod's database wiring built and live (`KAV-32`, Lab 22) |
| `deploy/environments/local/`, `staging/`, `prod/`, `lab-eks/` | One settings file per environment. **These are the only differences between environments.** The code and the chart are identical everywhere (ADR-0004). `staging` and `prod` were first rehearsed as two Helm releases in the *same* k3d cluster `local` runs in ([ADR-0023](adr/0023-promotion-rehearsal-on-k3d.md)); both now describe real AWS clusters, `staging` since `KAV-57` | `local` working (`KAV-46`); `prod` live (`KAV-51`); `staging` proven live (`KAV-57`, Lab 29); `lab-eks` in Phase 8 |
| `deploy/dashboards/` | Dashboards kept as files. `kaval-health.json` is a Grafana dashboard for Kaval's own health, shown by a viewer on the dev server or the laptop against a port-forward, never on the node | Working, imports cleanly (`KAV-78`) |
| `deploy/gitops/` | Flux configuration. The cluster pulls its setup from Git and rebuilds itself if the server is lost | Working, applied in AWS (`KAV-51`, ADR-0025, Lab 20) — `prod/` holds the `GitRepository` + `HelmRelease` the node's cloud-init bootstraps at boot |

---

## Proving it works

| Path | What it will do | Status |
|---|---|---|
| `evals/` | `golden.py`: 20 known incidents (routine, adversarial, sparse, recurrence), built independently so they can't collide on one fingerprint. `run.py` runs each through correlate → context → diagnose → policy and scores it (`scoring.py`); action safety and schema validity are hard gates. `make evals [ONLY=name] [PROMPT=before|after|both]` ([ADR-0018](adr/0018-eval-harness-and-golden-incidents.md)). `baseline.py` is the frozen pre-`KAV-84` prompt and schema for before-and-after runs ([Lab 51](labs/lab-51-teaching-the-model-the-fixes.md)); `test_baseline.py` keeps it frozen | Working (`KAV-43`, `KAV-84`) |
| `chaos/` | The game-day side of breaking things on purpose, on staging only: the README (how a game day goes, what stops one, the five experiments) and `game-day-template.md`. The runner code is `services/executor/kaval_executor/chaos.py`, the chart objects `deploy/charts/kaval/templates/chaos.yaml`, the commands `scripts/ops/chaos.sh` (+ `chaos_propose.py`) | `oom` (`KAV-79`), `badrelease` (`KAV-80`), `slow` and `disk` (`KAV-81`) built; `node` is the laptop script `scripts/ops/chaos_node.py` |
| `docs/postmortems/` | One blameless write-up per game day: hypothesis before, timings and lessons after | First: 2026-10-11 oom |

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
| `docs/architecture/disaster-recovery.md` | For each store: what protects it, how much recent work could be lost (RPO) and how long to get back (RTO), each number with where it came from, and the two gaps found (Terraform state only on the laptop; the dump skips parked nights) (`KAV-82`, [ADR-0049](adr/0049-backups-watched-and-restores-drilled.md)) | Working |
| `docs/architecture/service-levels.md` | What Kaval promises about its own behaviour (detect, diagnose, time to restore, resolution, safety) and how each is measured; provisional until the first game day | Working (`KAV-77`, [ADR-0041](adr/0041-service-levels-and-recording-whether-a-fix-held.md)) |
| `docs/architecture/mttr-runs.json` · `mttr.svg` | Every chaos run that has a time to restore, copied from its post-mortem, and the chart drawn from it (stages stacked: detect, diagnose, the person, queue, execute). Staging is destroyed after a game day and takes `make mttr` with it, so the record lives here (`KAV-83`, ADR-0051) | Working, tested |
| `docs/architecture/model-shortlist.md` | The local AI models measured on the prod-sized server: memory, speed, output-format results, and which three go to the Phase 2 evals (`make bench` reproduces it) | Working (`KAV-22`) |
| `docs/architecture/architecture.toml` | The system's architecture written as data: every component and connection, and which phase it arrives in. The dashboard draws the three diagrams from this. Update it whenever a component is added, removed or rewired (Definition of Done item 6) | Working |
| `docs/architecture/diagrams/` | Exported diagram images. The live diagrams come from `docs/architecture/architecture.toml` and appear on the dashboard | Placeholder, Phase 8 |
| `docs/drills/` | One dated record per restore drill, written by `make restore-drill`; the daily backup check reads the file names ([ADR-0049](adr/0049-backups-watched-and-restores-drilled.md)) | Working (`KAV-82`) |
| `docs/runbooks/` | **Troubleshooting guides**, one per failure type. Written for humans, **and** the AI agent reads them when diagnosing incidents | Working (4 runbooks, including [asked to remove waste, or a removal failed or stalled](runbooks/waste-removal.md), [waste found](runbooks/waste-found.md) and [the waste scan is not running](runbooks/waste-scan-not-running.md), [the cost reader is not getting the bill](runbooks/cost-reader-not-pulling.md), [bringing production up and parking it](runbooks/prod-rollout.md), [the still-alive alarm](runbooks/still-alive-alarm.md), [Kaval itself has stopped](runbooks/kaval-stopped-reporting.md) and [out-of-memory kills](runbooks/pod-oomkilled.md)); more in Phase 5 |
| `docs/cost/budget-plan.md` | Every expected cost, the $50/month ceiling, and how it's enforced | Working |
| `docs/cost/actuals/` | The real bill, one file per month | Placeholder. Starts with the first real spend, in Phase 4 |
| `docs/releases/` | A change record for every production release, generated by `promote.yml`: what changed, who approved it, how to roll back | Working (`KAV-67`); format in `README.md`, first record `2026-10-09-v0.1.0.md` (backfilled) |
| `docs/course/outline.md` | The plan for turning the labs into a course or video series | Working |
| `docs/course/episode-scripts/` | Scripts for each episode | Placeholder, Phase 8 |
| `docs/dashboard.html` | The generated dashboard page. Not in Git; the published copy is the real one | Generated |

### `scripts/`: helper programs, grouped by what they touch

| Path | What it does | Status |
|---|---|---|
| **`scripts/ops/`** | **AWS and the database** | |
| `scripts/ops/cost-report.sh` | Month-to-date AWS spend against the $50 ceiling (`make cost-report`) | Working |
| `scripts/ops/tag_policy.py` · `tag_scan.py` · `showback.py` · `test_tag_policy.py` · `test_tag_scan.py` · `test_showback.py` | The tag policy (`KAV-93`, [ADR-0061](adr/0061-every-resource-carries-three-tags-and-the-bill-is-split-by-them.md)): `tag_policy.py` holds the three required labels and the offline check CI runs over every environment, the server module and both stop jobs; `tag_scan.py` (`make tag-scan`) lists Kaval resources in the live account missing a label, read-only, counting but never touching the other project's; `showback.py` (`make cost-showback`) writes the month's bill by AWS service and environment to `docs/cost/showback-<month>.md` for $0.01. Tested on made-up resources and bills, with the negative cases | Built, tested; staging proof in Lab 58 |
| `scripts/ops/backup_watch.py` · `test_backup_watch.py` | The daily check's rules as tested functions: which 19:30 UTC nights a dump is owed (the app server's uptime from the auto-scaling history), a dump that is missing, truncated or shrunk, a restore drill overdue (`KAV-82`, ADR-0049) | Built |
| `scripts/ops/restore-drill.sh` · `restore_drill_record.py` · `test_restore_drill_record.py` | `make restore-drill`: restores production's newest dump strictly into a throwaway Postgres, counts rows, then the record script decides pass or fail and writes `docs/drills/restore-<date>.md` (a failed drill is `restore-failed-<date>.md`). Production is only read (`KAV-82`, ADR-0049) | Built; first drill 2026-10-13 |
| `scripts/ops/approvals_drill.py` | Staging rehearsal of an unanswered approval (`make approvals-file`, `make approvals-report`): files one harmless `ask`-class request with the agent's role, then shows its age and the exact reminder, escalation or expiry message the real notifier would send, as a rolled-back dry run (staging has no Slack) (`KAV-82`, ADR-0048) | Built |
| `scripts/ops/chaos_check.py` · `test_chaos_check.py` | After the `slow` or `disk` game day (`make chaos-check`, read-only, with the gateway's database role): was a person told and was nothing proposed or run automatically? Staging has no Slack, so it runs the real alert code as a dry run (rolled back) and prints the message. The verdict rule is a tested function (`KAV-81`, ADR-0047) | Built; run live on staging |
| `scripts/dev/test_gitops_values_in_sync.py` | Fails whenever an environment's two copies of its settings differ: `deploy/environments/<env>/values.yaml` and the inline `values:` of the HelmRelease Flux deploys (`KAV-81`: the first slow-service run found a setting that never reached the cluster) | Built |
| `scripts/dev/mttr_chart.py` · `test_mttr_chart.py` | Redraws `docs/architecture/mttr.svg` from `mttr-runs.json`; the test refuses numbers whose stages do not add up to the recorded totals and a picture that is out of date (`KAV-83`) | Working, tested |
| `scripts/dev/test_budget_wiring.py` | The spending brake's numbers must agree everywhere: production's committed Terraform defaults, the module's defaults, `CLAUDE.md` and the budget plan, and the brake must be armed and name production's and staging's server groups (`KAV-96`, [ADR-0064](adr/0064-the-spending-brake-names-every-server-group-and-is-proven-on-staging.md)) | Working |
| `scripts/dev/test_chart_probes.py` | Renders the real chart for staging, prod and local and checks the readiness check on the agent, executor, collector and poller (and that none has a restart-on-failure check) (`KAV-83`, ADR-0051) | Working, tested |
| `scripts/dev/test_migration_rules.py` | The rules that let a new version start on an old database: the migration runs before the install when the database is a server of its own, every migration only adds (a deliberate exception carries `# contract: <why>`), every created table has a `GRANT`, and the node's bootstrap hands out permissions again once the release is Ready ([ADR-0057](adr/0057-the-database-is-brought-up-to-date-before-a-new-version-starts.md), `KAV-85`) | Working |
| `scripts/dev/test_cost_pull.py` · `test_chart_cost.py` | The server's cost-pull script run against stand-ins (one request as the borrowed login only; none when a good answer is fresh or the cluster is not ready; stops at the cap of 60 and allows the 60th; a failure passes on one fixed word and never AWS's text), the role's one permission, the 16 KB start-up-script limit, and the chart's cost reader (absent unless switched on, may get one ConfigMap by name, no AWS login) (`KAV-92`, ADR-0060) | Working, tested |
| `scripts/dev/test_node_heartbeat.py` · `test_still_alive_alarm.py` | The node's still-alive script run against stand-ins (every unclear case sends nothing; the freshness limit and the service match are mutation-checked), and the alarm's settings (the rule, the fills that lean toward ringing, 5 x 5 minutes, both e-mails, the one-namespace permission) (`KAV-83`, ADR-0053) | Working, tested |
| `scripts/tracking/test_dashboard_labs.py` | Every lab appears on its phase's row of the dashboard by itself (the phase is read from the lab's own header), and "paused" is explained as a cost posture (`KAV-83`) | Working, tested |
| `scripts/ops/chaos_node.py` · `test_chaos_node.py` | Game day: lose the app server. Laptop-run (the cluster cannot test its own death): ends staging's app machine through its auto-scaling group, times the replacement and checks no data was lost. Refuses anything not tagged staging; you type the machine id. Behind `make chaos-node` (`KAV-80`, ADR-0046) | Built; run live on staging |
| `scripts/ops/approve.py` | Approve or deny one proposed action through the gateway's decision endpoint (`make approve`) — the Phase-3 stand-in for the mobile app's swipe-to-approve screen | Working (`KAV-47`) |
| `scripts/ops/backup.sh` | Nightly database dump to S3. Run by a systemd timer on the app node (`KAV-74`, [ADR-0038](adr/0038-nightly-dump-is-taken-by-the-app-node.md)); also runnable by hand before something risky. Checks the dump can be read back before uploading it | Proven locally against a real Postgres (Lab 40) and run once on the real node (2026-10-10: dump uploaded, `LATEST` moved); the timer firing on its own is unseen. The chart `CronJob` that was meant to run it stays disabled |
| `scripts/ops/restore.sh` | Restores a backup into staging (made anonymous first) or, in an emergency, into production. Staging mode refuses the production host and, if the scrub fails, drops the unscrubbed copy ([ADR-0037](adr/0037-staging-seeding-from-the-production-dump.md)) | Hardened and run end to end on a local database against a real dump (`KAV-72`, Lab 39). The staging node runs it at boot on the cleaned copy (`RESTORE_PREFIX=seed`, `--no-privileges`, [ADR-0039](adr/0039-staging-is-filled-from-a-copy-cleaned-on-the-laptop.md), `KAV-73`, Lab 41); proven locally and live on staging 2026-10-10 |
| `scripts/ops/anonymise.sql` | Strips personal and secret data from a copy before staging gets it. One transaction; its final check is that scrubbing again would change nothing ([ADR-0037](adr/0037-staging-seeding-from-the-production-dump.md)) | Rewritten against the real schema and tested in CI (`services/shared/tests/test_anonymise.py`, `KAV-72`, Lab 39) |
| `scripts/ops/seed-refresh.sh` | `make seed-refresh`: on your own machine, restores production's newest dump into a throwaway Postgres in Docker, scrubs it, checks the result a second, independent way, and writes `.build/seed/clean.dump` (the raw copy is deleted). `make staging-up` hands that file to staging; staging never reads production's bucket ([ADR-0039](adr/0039-staging-is-filled-from-a-copy-cleaned-on-the-laptop.md), `KAV-73`) | Proven locally (three refusals) and run on the real production dump 2026-10-10 (Lab 41) |
| `scripts/ops/db-roles.sql` | Creates the four per-service Postgres roles and their `GRANT`s, idempotently (`KAV-32`, ADR-0008) | Working, live, automatic — the original Helm hook Job was blocked and is superseded: the node's own boot/6h bootstrap script runs this instead (`KAV-56`, ADR-0027, Lab 23) |
| `scripts/ops/pause-database.sh` / `resume-database.sh` | Stop/start the database instance (never terminate), snapshotting the data volume first on pause. Wired into `make down`/`make up` (`KAV-32`, ADR-0008, Lab 24) | Working, live since 2026-10-05 |
| `scripts/ops/health-check-database.sh` | `pg_isready` + a sanity query over SSM. Run from `resume-database.sh`, or standalone (`make db-health-check`). Never restores anything itself — on failure it prints the restore command and exits non-zero (`KAV-32`, ADR-0008, Lab 26) | Working, live |
| `scripts/ops/restore-snapshot.sh` | Restores the data volume from an EBS snapshot (`make db-restore-snapshot SNAPSHOT=<id>`, or the newest one). Detaches the damaged volume (kept, tagged `Reason=damaged`), attaches a new one, reconciles Terraform state (`KAV-32`, ADR-0008, Lab 26) | Written and reviewed; not yet run against prod — the live restore test is deferred, same as the pause/resume scripts' |
| `scripts/ops/chaos.sh` · `chaos_propose.py` | A staging game day: `run` (switch on, start the Job, follow it, switch off), `stop`, `status`, `propose` (staging has no model: files the diagnosis; the real policy engine still classifies it), `approve` (the human step), `mttr`. Behind `make chaos-run`/`chaos-stop`/`chaos-status`/`chaos-propose`/`chaos-approve`/`chaos-mttr` (`KAV-79`) | Working, run live on staging 2026-10-11 |
| `scripts/ops/tf-init.sh` | Connects an environment to Terraform's remote record: reads the bucket's name from Parameter Store (it is not in git) and runs `terraform init`. `make tf-init ENV=prod`. The whole recovery on a new laptop |
| `scripts/dev/test_terraform_state.py` | The rules that keep Terraform's record off the laptop: every environment names the S3 backend with the lock on and its own key, no `.tfstate` is committed, the bucket's real name is in no file, the bucket keeps its protections (`KAV-86`) |
| `scripts/ops/staging.sh` | `up`, `status` and `down` for the staging cluster, behind `make staging-up`/`staging-status`/`staging-down`. `up` resumes a parked database, applies `infra/envs/staging` and waits for the pods; `down` stops the database, releases its protected data volume from state, destroys, deletes the volume and staging's snapshots, and counts what is left tagged `Env=staging` (`KAV-59`, ADR-0029, Lab 30). On a fresh staging `up` also uploads the cleaned copy to staging's bucket and prints the node's seed status (`KAV-73`, ADR-0039) | Working; the seed steps ran live 2026-10-10 (Lab 41) |
| **`scripts/tracking/`** | **Jira, Confluence and the dashboard** | |
| `scripts/tracking/dashboard.py` | Builds the progress dashboard from the repo (`make dashboard`) | Working |
| `scripts/tracking/dashboard.toml` | Things the dashboard can't work out by itself: links, cost figures and budget thresholds, per-phase Jira/Confluence links | Working |
| `scripts/tracking/publish-confluence.py` | Publishes the repo's docs to the Confluence space (`make docs-sync` runs both) | Working |
| `scripts/tracking/jira-sync.py` | Lists, creates and moves Jira stories; ticks their acceptance criteria; records UAT verdicts (`uat pass\|fail`, [ADR-0012](adr/0012-user-acceptance-testing.md)); fills the Service field from the code each story changed (`backfill-service`); creates a product version's Jira Release and files its issues in it (`release X.Y.Z`, `make jira-release`). `make jira EPIC=KAV-6` | Working |
| `scripts/tracking/atlassian.py` | The Jira/Confluence login code, shared by the two scripts above | Working |
| **`scripts/release/`** | **Moving a build between environments** | |
| `scripts/release/pin_staging.py` | Rewrites staging's image tags (the Flux HelmRelease and the environment values file together) to a new `sha-<short>`; refuses to half-pin if either file's shape changed. Called by `release.yml`; tests in `test_pin_staging.py` ([ADR-0030](adr/0030-ci-publishes-images-by-oidc-and-staging-is-pinned-by-pull-request.md)) | Working (`KAV-61`) |
| `scripts/release/staging_smoke.py` | `make staging-smoke`: against a staging that is up, checks pods, restarts, the tag, the **running digest against ECR**, the gateway's database and migration revision and the REST reads, over Session Manager (read-only). On a pass it appends to `deploy/promotion/passed-staging.json`. Tests in `test_staging_smoke.py` ([ADR-0031](adr/0031-staging-smoke-test-and-the-passed-staging-record.md), [Lab 33](labs/lab-33-staging-smoke-and-passed-record.md)) | Working (`KAV-62`) |
| `scripts/release/versions.py` | Works out each component's next version from the conventional commits since its last tag (`plan`, `bump`), tags `<svc>-vX.Y.Z` and `vX.Y.Z` (`tag`), and prints a version set (`show`); `make version-plan`. Which files ship in which image is checked against the Dockerfiles by `test_versions.py` ([ADR-0035](adr/0035-computed-versions-tags-and-the-generated-change-record.md)) | Working (`KAV-67`) |
| `scripts/release/change_record.py` | Writes `docs/releases/<date>-vX.Y.Z.md` from Git, the passed-staging record and the gate's output: components, changes, Jira keys, derived risk, staging evidence, rollback plan; `promote.yml` puts it in the promotion pull request (`test_change_record.py`) | Working (`KAV-67`) |
| `scripts/release/promote.py` | The promotion gate's brain (plus `test_promote.py`): `verify` asks whether a tag may go to prod (on the passed-staging record, ECR digests still equal, ahead of prod, every `uat` story signed off in Jira), `pin` rewrites prod's two files, `body` writes the pull-request text, `guard` fails a pull request that moves prod's tags to a tag not on the record, `preflight` (run first by `make up`) fails if the images prod pins have been cleaned out of ECR | Working (`KAV-63`; `preflight` `KAV-68`) |
| `deploy/promotion/passed-staging.json` | The record of which digests passed staging: tag, commit, time, the four digests, the checks, and what the pass does not prove. Written by `make staging-smoke`, committed through a pull request; `promote.yml` refuses any digest not in it | Working (`KAV-62`) |
| **`scripts/dev/`** | **Setting up your own machine** | |
| `scripts/tracking/jira-dashboards.toml` + `jira-dashboards.py` | The Jira dashboards (Delivery, UAT, Releases) and their filters, as data; the script creates or updates them, and a second run changes nothing (`make jira-dashboards`) | Working (`KAV-36`) |
| `scripts/tracking/jira_release.py` | Reads a release's change record (`docs/releases/`) into the name, description, date and Jira keys of a Jira Release; no network, so it's unit-tested in `test_jira_release.py`. Used by `jira-sync.py release` / `make jira-release` | Working (`KAV-35`) |
| `scripts/tracking/jira_adf.py` | Builds and reads Jira story descriptions (Context, Acceptance Criteria, UAT scenarios) and maps code folders to the Service field; no network, so it's unit-tested in `test_jira_adf.py` | Working (`KAV-34`) |
| `scripts/dev/install-hooks.sh` | Installs the two Git hooks: `pre-commit` (blocks secrets) and `commit-msg` (the commit convention) | Working |
| `scripts/dev/check_commits.py` | The commit-convention checker, used by the `commit-msg` hook and by CI on every PR; tests in `test_check_commits.py` ([ADR-0011](adr/0011-commit-convention-and-jira-link.md), [Lab 07](labs/lab-07-commits-and-jira-link.md)) | Working (`KAV-25`) |
| `scripts/dev/rules_test.sh` | Runs the alert rules through `promtool` using the Prometheus image the chart pins (`make rules-test`; CI runs it) | Working (`KAV-78`) |
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
| `.env` | Secrets. The repo is public (since 2026-09-30), and Git history is permanent |
| `infra/envs/*/terraform.tfvars` | Your real email, SSH public key and settings |
| `infra/envs/*/terraform.tfstate*` | Old local copies of Terraform's record. Since `KAV-86` (2026-10-16) the real record is in a private S3 bucket ([ADR-0058](adr/0058-terraforms-record-lives-in-a-private-versioned-bucket.md)); the files here are renamed `*.migrated-2026-10-16` or old `.backup`s, kept a week, and can then be deleted. They contain account details and passwords |
| `~/.ssh/kaval-devbox` | The dev server's SSH private key. It lives in your home folder, outside the project entirely |
| `.venv/` | Installed Python packages. Rebuild with `make sync` (exactly what `uv.lock` pins) |
| `docs/dashboard.html` | Regenerated every time |
| Caches (`__pycache__`, `.mypy_cache`, `.terraform/`...) | Rebuilt automatically |

---

When a folder is added, moved or starts being used, update this guide in the same change.
