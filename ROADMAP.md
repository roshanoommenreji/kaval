# Roadmap

Nine phases, ~28 weeks at 4–6 hrs/week, sharing evenings with AI-103 study.

Every phase ends in something demonstrable. Every phase can be abandoned mid-way and resumed
without lost state. **Stopping after Phase 6 still yields a strong portfolio piece** — Phases 7–9
are additive, not load-bearing.

Legend: `[ ]` not started · `[~]` in progress · `[x]` done

---

## Phase 0 — Foundations · weeks 1–2 · `[x]`

Guardrails before anything that can cost money.

- [x] Repository skeleton and folder layout
- [x] `CLAUDE.md` — conventions for Claude Code in this repo
- [x] `docs/00-start-here.md`, ADR process established
- [x] ADR-0001 record decisions · ADR-0002 k3s + EKS · ADR-0003 region
- [x] `.gitignore`, `.gitattributes`, `.env.example`, gitleaks pre-commit hook
- [x] `git init` + first commit
- [x] `infra/modules/budget` — alerts at $18/$22, hard-stop Lambda at $24, applied 2026-09-15 (now $30/$35/$38 with the $40 ceiling, ADR-0008)
- [x] Progress dashboard — `scripts/tracking/dashboard.py`, derived from this file
- [x] Learning layer — a concept page per phase in `docs/learn/`
- [x] Architecture diagrams — `docs/architecture/architecture.toml`, system + journey + delivery views
- [x] ADR-0004 environment strategy and promotion path
- [x] ADR-0005 data durability and staging seeding
- [x] Jira Cloud free tier, project `KAV`, 10 epics
- [x] **Fix AWS credentials** — new IAM user, MFA, named profile
- [x] `terraform apply` the budget module and fire a test alert

**Exit gate:** `terraform apply` has created only a budget alarm, and it demonstrably fires. ✅
10 resources created (SNS topic, policy, 2 subscriptions, budget, IAM role, IAM role policy,
Lambda, permission, log group) — nothing billable. Lambda invoked manually, CloudWatch log
confirms `dry_run=True`, correct warning message. **Phase 0 complete 2026-09-15.**

---

## Phase 1 — Local first · weeks 3–5 · `[~]`

Prove the loop on the AWS dev server ([ADR-0007](docs/adr/0007-develop-on-an-aws-dev-server.md),
changed from "the laptop" on 2026-09-26). About $5/month, and it stops itself when idle.

- [x] AWS dev server — `infra/envs/dev`, `t4g.medium`, SSM-only access, idle stop, `make devbox-*` (applied and verified 2026-09-26, Lab 03)
- [x] Decision: production database on its own server, ceiling $25 → $40 ([ADR-0008](docs/adr/0008-production-database-on-its-own-server.md), `KAV-31`; the server itself is built in Phase 4)
- [ ] `docker-compose` — Ollama + local model shortlist (Gemma 3 1B · Gemma 4 E2B · Qwen3 1.7B · Llama 3.2 1B), Postgres + pgvector, gateway. Model memory is measured **without** Postgres, since prod won't host it on the app node
- [ ] Synthetic signal generator (fake pod crashes, cost spikes)
- [x] Data model migrations: signal · incident · proposal · action · decision · execution · outcome
- [ ] Gateway REST skeleton, health checks, OpenAPI
- [ ] `.github/workflows/ci.yml` — ruff · mypy · pytest · gitleaks · arm64 build · Trivy
- [ ] Jira project `KAV` workflow: Backlog → Ready → In Progress → In Review → In Staging → Ready for Prod → Done
- [ ] Conventional commits + semantic versioning + Jira smart commits
- [ ] `docs/learn/phase-1-local-first.md` — flip **Written from** to `experience`

**Exit gate:** a fake incident flows end-to-end and lands in the database.

---

## Phase 2 — The agent loop · weeks 6–9 · `[ ]`

The hard, interesting part. Still no AWS.

- [ ] Signal correlation → incident fingerprinting
- [ ] Context builder: runbook RAG (pgvector) + past incidents + recent changes
- [ ] JSON-schema-enforced proposal output — the model must not ramble
- [ ] Policy engine: `auto` / `ask` / `never`, blast-radius classification
- [ ] Jev risk rating feeding the policy engine (ADR-0006, `KAV-27`) — `typesafe-sdk`, pinned version, redaction, rules-only fallback
- [ ] Eval harness + 20 golden incidents — including Jev calibration vs the rules-only baseline
- [ ] Bedrock escalation path for low-confidence cases
- [ ] `docs/learn/phase-2-the-agent-loop.md` — flip **Written from** to `experience`

**Exit gate:** 20 synthetic incidents produce valid, sane proposals; evals pass.

---

## Phase 3 — Kubernetes local · weeks 10–12 · `[ ]`

- [ ] k3d cluster on the dev server
- [ ] Helm umbrella chart, `local` values
- [ ] Real K8s events + Prometheus as signal sources
- [ ] Executor with scoped RBAC — the privilege split made real
- [ ] **Executor redacts `stdout` at write time** — prod must never store a secret (ADR-0005)
- [ ] `arm64` multi-arch image builds *(Graviton is coming in Phase 4)*
- [ ] Split values: `deploy/environments/staging` and `prod`, each pinning image digests
- [ ] Promotion mechanics rehearsed on k3d — deploy staging, gate, deploy prod
- [ ] **First rollback drill, timed** — `helm rollback`, record time-to-restore
- [ ] `docs/learn/phase-3-kubernetes-local.md` — flip **Written from** to `experience`

**Exit gate:** kill a pod locally → agent proposes → you approve → executor fixes it.

---

## Phase 4 — AWS landing · weeks 13–15 · `[ ]`

First real spend. **Posture: paused between sessions** (`make down`).

- [ ] Terraform: network, spot node, ECR, IAM
- [ ] k3s bootstrap via cloud-init
- [ ] Flux GitOps reconciliation
- [ ] Cloudflare Tunnel — no ALB, no NAT Gateway
- [ ] **Database server** — `infra/modules/database` ([ADR-0008](docs/adr/0008-production-database-on-its-own-server.md), `KAV-32`): `t4g.small` on-demand, separate encrypted 20 GB data volume (`prevent_destroy`), security group allowing 5432 only from the app node, SSM only (no SSH), termination protection, Postgres TLS, passwords in SSM Parameter Store, per-service roles, graceful shutdown (`stop_grace_period: 60s`)
- [ ] DB backups — DLM daily EBS snapshots (keep 7) + nightly dump to S3 (`scripts/ops/backup.sh`, RPO 24 h)
- [ ] **Pre-stop snapshot** in every stop path — `make down`, the nightly auto-stop and the hard-stop Lambda (`ec2:CreateSnapshot` scoped to `Project=kaval`)
- [ ] **Nightly auto-stop** at 02:00 IST while paused — EventBridge Scheduler, switched off by a Terraform variable from Phase 7
- [ ] **Start-up health check** — `pg_isready` + sanity query on `make up` and at boot; restore the latest pre-stop snapshot **only on failure** (`make db-restore-snapshot`, per the restore runbook)
- [ ] `scripts/ops/restore.sh` + `anonymise.sql` — staging seeded from a sanitised prod snapshot
- [ ] **Restore drill** — measured RTO recorded, and `restore-from-backup` runbook verified
- [ ] `make up` / `make down`
- [ ] `infra/envs/staging` — second spot node, own VPC, own k3s, 10 GB EBS, **plus its own database server** from `infra/modules/database`
- [ ] `make staging-up` / `staging-down`, self-destruct after 4 idle hours
- [ ] `release.yml` — build once, push by digest, deploy staging, smoke test, release notes
- [ ] `promote.yml` — the gate. **Refuses a digest that did not pass staging**
- [ ] `rollback.yml` — measured time-to-restore
- [ ] Generated change records in `docs/releases/`
- [ ] `docs/learn/phase-4-aws-landing.md` — flip **Written from** to `experience`

**Exit gate:** terminate the node by hand; it rebuilds itself from Git in under 5 minutes.

---

## Phase 5 — Mobile app · weeks 16–19 · `[ ]`

**Posture: paused between sessions.**

- [ ] Expo app scaffold, TypeScript, navigation
- [ ] Cognito auth
- [ ] Screens: Pulse · Inbox · Detail · Timeline · Ask · Settings
- [ ] Push notifications via Expo
- [ ] Approve / deny round trip
- [ ] `docs/learn/phase-5-mobile-app.md` — flip **Written from** to `experience`

**Exit gate:** phone buzzes for a real incident and you approve it from bed.

---

## Phase 6 — Chaos + proof · weeks 20–21 · `[ ]`

**Posture: paused between sessions.**

- [ ] Chaos CronJobs: OOM kill · crashloop · disk fill · latency injection · node drain
- [ ] Blast-radius containment — chaos confined to a labelled namespace
- [ ] MTTR measurement and dashboard
- [ ] Database server monitoring — `postgres_exporter` + `node_exporter` → Prometheus, disk-space alert; SSM Patch Manager schedule; connection and failed-auth logging reviewed (ADR-0008)
- [ ] Runbooks written for each failure class *(also the agent's RAG corpus)*
- [ ] `docs/learn/phase-6-chaos-and-proof.md` — flip **Written from** to `experience`

**Exit gate:** five chaos types handled end-to-end, MTTR charted.

---

## Phase 7 — FinOps domain · weeks 22–24 · `[ ]`

**Posture: always-on from here.** Cost Explorer needs real days of data.

- [ ] Cost Explorer + CUR ingestion
- [ ] Tagging strategy and enforcement
- [ ] Waste detection: idle nodes, orphaned volumes, unused snapshots
- [ ] Cost proposals through the same approve/execute path
- [ ] Hard-stop Lambda wired to the live ASG
- [ ] `docs/learn/phase-7-finops.md` — flip **Written from** to `experience`

**Exit gate:** the agent finds real waste in your own account and you approve the fix.

---

## Phase 8 — EKS chapter · week 25 · `[ ]`

- [ ] `infra/envs/lab-eks` — real EKS via Terraform
- [ ] IRSA for the executor — no static keys
- [ ] AWS Load Balancer Controller
- [ ] Deploy the **identical** Helm chart, unmodified
- [ ] Screenshot and record everything
- [ ] `terraform destroy`, verified clean 24 h later
- [ ] `docs/learn/phase-8-eks-chapter.md` — flip **Written from** to `experience`

**Exit gate:** same chart runs on EKS with zero edits; teardown leaves zero billable resources.

---

## Phase 9 — Harden & publish · weeks 26–28 · `[ ]`

- [ ] Security pass — Trivy, image signing, RBAC audit
- [ ] gitleaks scan over full history
- [ ] Architecture diagrams
- [ ] README rewrite for a public audience
- [ ] Demo video
- [ ] **Repo public**
- [ ] Resume bullets written from what actually shipped
- [ ] Jira retrospective; course outline from `docs/labs/`
- [ ] Switch the promotion gate to GitHub Environments with required reviewers *(needs a public repo)*
- [ ] `docs/learn/release-engineering.md` — flip **Written from** to `experience`
- [ ] `docs/learn/phase-9-harden-and-publish.md` — flip **Written from** to `experience`

**Exit gate:** repo public, video recorded, bullets written.

---

## Beyond v1 — future scope · `[ ]`

Not committed. Work that is out of scope for Phases 0–9 but worth doing afterward —
mostly the Forward Deployed / AI-platform readiness gaps a solo greenfield project can't
close on its own (integration mini-projects, demo craft, client-facing reps, a
multi-tenant chapter).

See [docs/future-scope.md](docs/future-scope.md).

---

## Definition of Done — every story

Documentation is a merge gate, not willpower.

- [ ] Code merged, CI green
- [ ] Lab doc in `docs/labs/` — reproducible by a stranger from zero
- [ ] ADR written *if a decision was made*
- [ ] Journal entry appended
- [ ] Cost impact noted in `docs/cost/`
- [ ] `docs/architecture/architecture.toml` updated if components changed
- [ ] **Deployed to staging and verified before prod** — never straight to prod
