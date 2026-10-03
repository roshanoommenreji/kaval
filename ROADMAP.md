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

## Phase 1 — Local first · weeks 3–5 · `[x]`

Prove the loop on the AWS dev server ([ADR-0007](docs/adr/0007-develop-on-an-aws-dev-server.md),
changed from "the laptop" on 2026-09-26). About $5/month, and it stops itself when idle.

- [x] AWS dev server — `infra/envs/dev`, `t4g.medium`, SSM-only access, idle stop, `make devbox-*` (applied and verified 2026-09-26, Lab 03)
- [x] Decision: production database on its own server, ceiling $25 → $40 ([ADR-0008](docs/adr/0008-production-database-on-its-own-server.md), `KAV-31`; the server itself is built in Phase 4)
- [x] `docker-compose` — Ollama + local model shortlist (Gemma 3 1B · Gemma 3 1B QAT · Llama 3.2 1B · Qwen3 1.7B; Gemma 4 E2B dropped, 7.2 GB), Postgres + pgvector, gateway. Model memory is measured **without** Postgres, since prod won't host it on the app node
- [x] Synthetic signal generator: 4 scenarios (OOM crashloop, exec-format, cost spike, idle volume) in real payload shapes, flagged `synthetic`, `make signals` (`KAV-23`, Lab 05)
- [x] Data model migrations: signal · incident · proposal · action · decision · execution · outcome
- [x] Gateway REST skeleton, health checks, OpenAPI: read-only `/v1` signals + incidents, cursor paging, `/docs` ([ADR-0009](docs/adr/0009-gateway-api-conventions.md), `KAV-23`, Lab 05)
- [x] `.github/workflows/ci.yml` — ruff · mypy · pytest on real Postgres · migrations up/check/down · gitleaks (full history) · terraform fmt/validate · native arm64 build · Trivy; `uv.lock`, SHA-pinned actions, Dependabot ([ADR-0010](docs/adr/0010-ci-pipeline-and-supply-chain.md), `KAV-24`, Lab 06)
- [x] Jira project `KAV` workflow: To Do → In Definition → Ready → In Progress → In Review → In Staging → Ready for Prod → Done (`KAV-17`; the staging states wait for Phase 4)
- [x] Conventional commits, enforced by a `commit-msg` hook and CI; Jira linked to GitHub by the `KAV-<n>` key, smart-commit commands declined (noreply commit email) ([ADR-0011](docs/adr/0011-commit-convention-and-jira-link.md), `KAV-25`, Lab 07)
- [x] `docs/learn/phase-1-local-first.md` — flip **Written from** to `experience` (`KAV-26`: what was assumed vs measured, and the interview answers rewritten from what was built)

**Exit gate:** a fake incident flows end-to-end and lands in the database. ✅
Met for signals 2026-09-27 (`KAV-23`): generator → Postgres → `/v1` API, verified on the dev server.
Grouping those signals into an `incident` row is correlation, the first step of Phase 2.
$0.13 of AWS usage, covered by credits. **Phase 1 complete 2026-09-27.**

---

## Phase 2 — The agent loop · weeks 6–9 · `[~]`

The hard, interesting part. Still no AWS.

- [x] UAT process: `uat` label, UAT scenarios, `jira-sync.py uat pass|fail` ([ADR-0012](docs/adr/0012-user-acceptance-testing.md), `KAV-34`)
- [x] Component versions and release naming: Kaval X.Y.Z with its components' versions ([ADR-0013](docs/adr/0013-component-versions-and-release-naming.md), `KAV-35`)
- [x] Jira dashboards as code: Delivery, UAT, Releases (`KAV-36`, signed off in UAT 2026-09-28)
- [x] Signal correlation → incident fingerprinting ([ADR-0014](docs/adr/0014-signal-correlation-and-incident-fingerprints.md), `KAV-39`, signed off in UAT 2026-09-28)
- [x] Context builder: runbook RAG (pgvector) + past incidents + recent changes ([ADR-0015](docs/adr/0015-context-builder-retrieval-design.md), `KAV-40`, signed off in UAT 2026-09-28)
- [x] JSON-schema-enforced proposal output — the model must not ramble ([ADR-0016](docs/adr/0016-json-schema-enforced-proposal-output.md), `KAV-41`, signed off in UAT 2026-09-28)
- [x] Policy engine: `auto` / `ask` / `never`, blast-radius classification ([ADR-0017](docs/adr/0017-opa-policy-engine-and-earned-autonomy.md), `KAV-42`, signed off in UAT 2026-09-29)
- [ ] Jev risk rating feeding the policy engine (ADR-0006, `KAV-27`) — `typesafe-sdk`, pinned version, redaction, rules-only fallback
- [x] Eval harness + 20 golden incidents: schema validity and action safety as hard gates; root-cause keywords, calibration and cost reported ([ADR-0018](docs/adr/0018-eval-harness-and-golden-incidents.md), `KAV-43`, signed off in UAT 2026-09-30) — Jev calibration vs the rules-only baseline and escalation precision wait for `KAV-27`/Bedrock below
- [ ] Bedrock escalation path for low-confidence cases ([ADR-0019](docs/adr/0019-bedrock-escalation-and-the-mantle-client-rejection.md), `KAV-44` — built and unit-tested; blocked live on an AWS Marketplace payment issue, not yet UAT-signed-off)
- [ ] `docs/learn/phase-2-the-agent-loop.md` — flip **Written from** to `experience`

**Exit gate:** 20 synthetic incidents produce valid, sane proposals; evals pass.

---

## Phase 3 — Kubernetes local · weeks 10–12 · `[~]`

- [x] k3d cluster on the dev server ([ADR-0020](docs/adr/0020-the-helm-chart-and-the-local-k3d-environment.md), `KAV-46`)
- [x] Helm umbrella chart, `local` values — Postgres, gateway, the correlate loop running continuously in real Kubernetes, verified live (`KAV-46`, Lab 15)
- [x] Real K8s events as a signal source — polled continuously, de-duplicated by count, proven live with a real unscripted pod failure ([ADR-0022](docs/adr/0022-real-kubernetes-events-as-signals.md), `KAV-48`, Lab 17)
- [ ] Prometheus as a signal source *(deliberately deferred, ADR-0022 — a second real source, not bundled into `KAV-48`)*
- [x] Executor with scoped RBAC — the privilege split made real, proven live with `kubectl auth can-i` ([ADR-0021](docs/adr/0021-the-executor-scoped-rbac-and-the-approval-write-path.md), `KAV-47`, Lab 16)
- [x] **Executor redacts `stdout` at write time** — prod must never store a secret (ADR-0005, closed by `KAV-47`)
- [x] `arm64` multi-arch image builds — CI already built and Trivy-scanned these natively on every PR; this phase's hardware is what proved it end-to-end: the first real images, pushed to ECR and running on the real Graviton node (`KAV-51`, [ADR-0025](docs/adr/0025-flux-gitops-and-ecr-bootstrap.md), Lab 20)
- [x] Split values: `deploy/environments/staging` and `prod`, each pinning image digests — rehearsed as two Helm releases on the local k3d cluster, not yet the real second AWS cluster ([ADR-0023](docs/adr/0023-promotion-rehearsal-on-k3d.md), `KAV-49`, Lab 18)
- [x] Promotion mechanics rehearsed on k3d — deploy staging, gate, deploy prod — proven live with a byte-for-byte image-digest match between the two releases (`KAV-49`, Lab 18)
- [x] **First rollback drill, timed** — `helm rollback`, record time-to-restore — 1.49s, with the honest finding that Kubernetes' own rollout default, not the rollback, was what kept the service up (`KAV-49`, Lab 18)
- [ ] `docs/learn/phase-3-kubernetes-local.md` — flip **Written from** to `experience`

**Exit gate:** kill a pod locally → agent proposes → you approve → executor fixes it.

---

## Phase 4 — AWS landing · weeks 13–15 · `[~]`

First real spend. **Posture: paused between sessions** (`make down`).

- [x] Terraform: network, spot node, ECR, IAM — live, `kubectl get nodes` reports `Ready` over the real SSM-tunnelled SSH path ([ADR-0024](docs/adr/0024-prod-landing-network-ecr-iam-node.md), `KAV-50`, Lab 19)
- [x] k3s bootstrap via cloud-init — checksum-verified binary, not the unauthenticated `curl | sh` installer (`KAV-50`, Lab 19)
- [x] Flux GitOps reconciliation — installed via checksum-verified binary in cloud-init, bootstrapped against this repo's own `main`; a replacement node (spot reclamation) now reconciles itself, which is the Phase 4 exit gate (`KAV-51`, [ADR-0025](docs/adr/0025-flux-gitops-and-ecr-bootstrap.md), Lab 20)
- [x] **Slack ChatOps approval** — gateway opens an outbound Socket Mode connection to Slack and receives Approve/Deny button clicks over it; no inbound port, no Cloudflare Tunnel, no domain (`KAV-55`, [ADR-0026](docs/adr/0026-slack-chatops-and-deferred-mobile.md), Lab 21 — verified live on the dev server's k3d cluster: a real crashloop incident, a Slack approval click, the executor acting within one second)
- [x] **Database server** — `infra/modules/database` ([ADR-0008](docs/adr/0008-production-database-on-its-own-server.md), `KAV-32`): `t4g.small` on-demand, separate encrypted 20 GB data volume (`prevent_destroy`), security group allowing 5432 only from the app node, SSM only (no SSH), termination protection, Postgres TLS, passwords in SSM Parameter Store, per-service roles, graceful shutdown (`stop_grace_period: 60s`) — Lab 22
- [~] DB backups — DLM daily EBS snapshots (keep 7, live once applied) built; nightly dump to S3 (`scripts/ops/backup.sh`) is wired as a chart CronJob but not yet enabled — it needs an `aws-creds` Kubernetes Secret refreshed from the node's own instance role, which this environment's safety classifier blocked as "credential materialization" when drafted as cloud-init; Roshan builds or approves that piece directly (Lab 22)
- [ ] **Pre-stop snapshot** in every stop path — `make down`, the nightly auto-stop and the hard-stop Lambda (`ec2:CreateSnapshot` scoped to `Project=kaval`)
- [ ] **Nightly auto-stop** at 02:00 IST while paused — EventBridge Scheduler, switched off by a Terraform variable from Phase 7
- [ ] **Start-up health check** — `pg_isready` + sanity query on `make up` and at boot; restore the latest pre-stop snapshot **only on failure** (`make db-restore-snapshot`, per the restore runbook)
- [ ] `scripts/ops/restore.sh` + `anonymise.sql` — staging seeded from a sanitised prod snapshot
- [ ] **Restore drill** — measured RTO recorded, and `restore-from-backup` runbook verified
- [ ] `make up` / `make down`
- [ ] `infra/envs/staging` — second spot node, own VPC, own k3s, 10 GB EBS, **plus its own database server** from `infra/modules/database`
- [ ] `make staging-up` / `staging-down`, self-destruct after 4 idle hours
- [ ] `release.yml` — build once, push by digest, deploy staging, smoke test, release notes; component versions bumped from the commits touching each component and tagged `<svc>-vX.Y.Z`, the product release `vX.Y.Z` named with its components' versions (ADR-0011, ADR-0013)
- [ ] `promote.yml` — the gate. **Refuses a digest that did not pass staging**, and a release with a `uat` story not yet signed off (ADR-0012)
- [ ] `rollback.yml` — measured time-to-restore
- [ ] Generated change records in `docs/releases/`
- [ ] `docs/learn/phase-4-aws-landing.md` — flip **Written from** to `experience`

**Exit gate:** terminate the node by hand; it rebuilds itself from Git in under 5 minutes.

---

## Phase 5 — Chaos + proof · weeks 16–17 · `[ ]`

**Posture: paused between sessions.**

- [ ] Chaos CronJobs: OOM kill · crashloop · disk fill · latency injection · node drain
- [ ] Blast-radius containment — chaos confined to a labelled namespace
- [ ] MTTR measurement and dashboard
- [ ] Database server monitoring — `postgres_exporter` + `node_exporter` → Prometheus, disk-space alert; SSM Patch Manager schedule; connection and failed-auth logging reviewed (ADR-0008)
- [ ] Runbooks written for each failure class *(also the agent's RAG corpus)*
- [ ] `docs/learn/phase-5-chaos-and-proof.md` — flip **Written from** to `experience`

**Exit gate:** five chaos types handled end-to-end, MTTR charted.

---

## Phase 6 — FinOps domain · weeks 18–20 · `[ ]`

**Posture: always-on from here.** Cost Explorer needs real days of data.

- [ ] Cost Explorer + CUR ingestion
- [ ] Tagging strategy and enforcement
- [ ] Waste detection: idle nodes, orphaned volumes, unused snapshots
- [ ] Cost proposals through the same approve/execute path
- [ ] Hard-stop Lambda wired to the live ASG
- [ ] `docs/learn/phase-6-finops.md` — flip **Written from** to `experience`

**Exit gate:** the agent finds real waste in your own account and you approve the fix.

---

## Phase 7 — EKS chapter · week 21 · `[ ]`

- [ ] `infra/envs/lab-eks` — real EKS via Terraform
- [ ] IRSA for the executor — no static keys
- [ ] AWS Load Balancer Controller
- [ ] Deploy the **identical** Helm chart, unmodified
- [ ] Screenshot and record everything
- [ ] `terraform destroy`, verified clean 24 h later
- [ ] `docs/learn/phase-7-eks-chapter.md` — flip **Written from** to `experience`

**Exit gate:** same chart runs on EKS with zero edits; teardown leaves zero billable resources.

---

## Phase 8 — Harden & publish · weeks 22–24 · `[ ]`

- [ ] Security pass — Trivy, image signing, RBAC audit
- [ ] gitleaks scan over full history
- [ ] Architecture diagrams
- [ ] README rewrite for a public audience
- [ ] Demo video
- [ ] **Repo public**
- [ ] Resume bullets written from what actually shipped
- [ ] Jira retrospective; course outline from `docs/labs/`
- [ ] Switch the promotion gate to GitHub Environments with required reviewers *(needs a public repo)*
- [ ] Protect `main`: require the CI checks to pass before a pull request merges *(free once public; a written rule until then, see docs/contributing.md)*
- [ ] `docs/learn/release-engineering.md` — flip **Written from** to `experience`
- [ ] `docs/learn/phase-8-harden-and-publish.md` — flip **Written from** to `experience`

**Exit gate:** repo public, video recorded, bullets written.

---

## Phase 9 — Mobile app · weeks 25–28 · `[ ]`

**Deferred, deliberately (2026-10-03).** Pushed to last because a custom mobile client is
unusual outside this kind of portfolio project — real companies approve incidents through
PagerDuty/Opsgenie or ChatOps, not a bespoke app. Phase 4 already ships Slack ChatOps
(`KAV-55`) as the real approval surface, so no later phase is blocked on this one existing.
Whether to build mobile at all, and how it would reach the gateway (the Cloudflare Tunnel +
domain this phase originally planned, or something else), is an open decision, revisited only
if this phase is actually picked up. See
[ADR-0026](docs/adr/0026-slack-chatops-and-deferred-mobile.md).

- [ ] Decide: build mobile at all, and if so, how it reaches the gateway
- [ ] Expo app scaffold, TypeScript, navigation
- [ ] Cognito auth
- [ ] Screens: Pulse · Inbox · Detail · Timeline · Ask · Settings
- [ ] Push notifications via Expo
- [ ] Approve / deny round trip
- [ ] `docs/learn/phase-9-mobile-app.md` — flip **Written from** to `experience`

**Exit gate:** phone buzzes for a real incident and you approve it from bed.

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
