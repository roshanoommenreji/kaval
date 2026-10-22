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
- [x] `infra/modules/budget` — alerts at $18/$22, hard-stop Lambda at $24, applied 2026-09-15 (now $42/$46/$48 with the $50 ceiling, ADR-0008 then ADR-0028)
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
- [x] `docs/learn/phase-2-the-agent-loop.md` — flip **Written from** to `experience` (2026-10-10: an assumed-vs-happened table from the labs, stale claims corrected, interview answers from what was built)

**Exit gate:** 20 synthetic incidents produce valid, sane proposals; evals pass.

---

## Phase 3 — Kubernetes local · weeks 10–12 · `[x]`

- [x] k3d cluster on the dev server ([ADR-0020](docs/adr/0020-the-helm-chart-and-the-local-k3d-environment.md), `KAV-46`)
- [x] Helm umbrella chart, `local` values — Postgres, gateway, the correlate loop running continuously in real Kubernetes, verified live (`KAV-46`, Lab 15)
- [x] Real K8s events as a signal source — polled continuously, de-duplicated by count, proven live with a real unscripted pod failure ([ADR-0022](docs/adr/0022-real-kubernetes-events-as-signals.md), `KAV-48`, Lab 17)
- [x] Prometheus as a signal source — a small Prometheus in the chart, polled for containers near their memory limit, proven live with the agent opening a `memory_pressure` incident on its own; 33 MiB measured against the ~400 MB budgeted ([ADR-0040](docs/adr/0040-prometheus-as-a-polled-signal-source.md), `KAV-75`, Lab 43). Staging and prod stay off until re-measured on a real node
- [x] Executor with scoped RBAC — the privilege split made real, proven live with `kubectl auth can-i` ([ADR-0021](docs/adr/0021-the-executor-scoped-rbac-and-the-approval-write-path.md), `KAV-47`, Lab 16)
- [x] **Executor redacts `stdout` at write time** — prod must never store a secret (ADR-0005, closed by `KAV-47`)
- [x] `arm64` multi-arch image builds — CI already built and Trivy-scanned these natively on every PR; this phase's hardware is what proved it end-to-end: the first real images, pushed to ECR and running on the real Graviton node (`KAV-51`, [ADR-0025](docs/adr/0025-flux-gitops-and-ecr-bootstrap.md), Lab 20)
- [x] Split values: `deploy/environments/staging` and `prod`, each pinning image digests — rehearsed as two Helm releases on the local k3d cluster, not yet the real second AWS cluster ([ADR-0023](docs/adr/0023-promotion-rehearsal-on-k3d.md), `KAV-49`, Lab 18)
- [x] Promotion mechanics rehearsed on k3d — deploy staging, gate, deploy prod — proven live with a byte-for-byte image-digest match between the two releases (`KAV-49`, Lab 18)
- [x] **First rollback drill, timed** — `helm rollback`, record time-to-restore — 1.49s, with the honest finding that Kubernetes' own rollout default, not the rollback, was what kept the service up (`KAV-49`, Lab 18)
- [x] `docs/learn/phase-3-kubernetes-local.md` — flip **Written from** to `experience` (2026-10-10: an assumed-vs-happened table from the labs, stale claims corrected, interview answers from what was built)

**Exit gate:** kill a pod locally → agent proposes → you approve → executor fixes it.

---

## Phase 4 — AWS landing · weeks 13–15 · `[~]`

First real spend. **Posture: paused between sessions** (`make down`).

- [x] Terraform: network, spot node, ECR, IAM — live, `kubectl get nodes` reports `Ready` over the real SSM-tunnelled SSH path ([ADR-0024](docs/adr/0024-prod-landing-network-ecr-iam-node.md), `KAV-50`, Lab 19)
- [x] k3s bootstrap via cloud-init — checksum-verified binary, not the unauthenticated `curl | sh` installer (`KAV-50`, Lab 19)
- [x] Flux GitOps reconciliation — installed via checksum-verified binary in cloud-init, bootstrapped against this repo's own `main`; a replacement node (spot reclamation) now reconciles itself, which is the Phase 4 exit gate (`KAV-51`, [ADR-0025](docs/adr/0025-flux-gitops-and-ecr-bootstrap.md), Lab 20)
- [x] **Slack ChatOps approval** — gateway opens an outbound Socket Mode connection to Slack and receives Approve/Deny button clicks over it; no inbound port, no Cloudflare Tunnel, no domain (`KAV-55`, [ADR-0026](docs/adr/0026-slack-chatops-and-deferred-mobile.md), Lab 21 — verified live on the dev server's k3d cluster: a real crashloop incident, a Slack approval click, the executor acting within one second)
- [x] **Database server** — `infra/modules/database` ([ADR-0008](docs/adr/0008-production-database-on-its-own-server.md), `KAV-32`): `t4g.small` on-demand, separate encrypted 20 GB data volume (`prevent_destroy`), security group allowing 5432 only from the app node, SSM only (no SSH), termination protection, Postgres TLS, passwords in SSM Parameter Store, per-service roles, graceful shutdown (`stop_grace_period: 60s`) — Lab 22
- [~] DB backups — DLM daily EBS snapshots (keep 7) **live and enabled** (a duplicate-tag bug had silently left the policy in `ERROR` since the original apply — never actually run — found and fixed 2026-10-05, Lab 24); nightly dump to S3 (`scripts/ops/backup.sh`) is now **a systemd timer on the app node** using its own instance role, 19:30 UTC / 01:00 IST, before the 02:00 IST auto-stop — no new credential, replacing the chart CronJob (which needed a live key in the cluster and was scheduled after the auto-stop, so it would never have run). **Built, proven locally and run once for real on 2026-10-10** ([ADR-0038](docs/adr/0038-nightly-dump-is-taken-by-the-app-node.md), `KAV-74`, Lab 40): a 22 KB dump landed in the bucket and `LATEST` moved; the timer firing by itself at 19:30 is the one thing still unseen. Production is parked most of the time, so "nightly" means nightly while it is running
- [x] **Pre-stop snapshot** in every stop path — `make down`, the hard-stop Lambda, and now the nightly auto-stop schedule (next line — it invokes the same Lambda) all snapshot the data volume before stopping it (`ec2:CreateSnapshot` scoped to `Project=kaval`, `scripts/ops/pause-database.sh`/`resume-database.sh`)
- [x] **Nightly auto-stop** at 02:00 IST while paused — EventBridge Scheduler, switched off by a Terraform variable from Phase 7 (`KAV-32`, [ADR-0008](docs/adr/0008-production-database-on-its-own-server.md) amended, Lab 25)
- [x] **Start-up health check** — `pg_isready` + sanity query on `make up` (live) and at boot (written, takes effect on the next instance replacement); failure stops and asks rather than auto-restoring (`make db-restore-snapshot`, per the restore runbook) — `KAV-32`, Lab 26
- [x] `scripts/ops/restore.sh` + `anonymise.sql` — staging seeded from a sanitised prod snapshot. **Scrub and restore script done and proven on a local database** (rewritten against the real schema: the Phase 0 version would have failed on its first run; one transaction, fails closed, 15 tests in CI; [ADR-0037](docs/adr/0037-staging-seeding-from-the-production-dump.md), `KAV-72`, Lab 39). **Wiring decided and built, proven locally (2026-10-10, [ADR-0039](docs/adr/0039-staging-is-filled-from-a-copy-cleaned-on-the-laptop.md), `KAV-73`, Lab 41):** the dump is cleaned on the laptop (`make seed-refresh`, Docker) and only the cleaned file goes into staging's own bucket; the staging node fills an empty database from it before any service starts; order restore, then migration, then permissions (run twice). **Run live on staging 2026-10-10:** restore in 2 s, 10 tables, migration at head, grants in place, four programs `Running`, `staging-down` clean (production's tables were empty, so no rows were scrubbed or migrated: refresh once production has traffic)
- [x] **Restore drill** — live, timed restore against prod from a real DLM snapshot; RTO ~3m51s
  from confirmation to verified-healthy; found and fixed two script bugs the drill surfaced
  (`KAV-32`, Lab 27)
- [x] **`make up` / `make down`** — live round trip against prod; found and fixed three more bugs,
  the worst of which meant `make down` would have destroyed the live database (`KAV-32`, Lab 28)
- [x] **Prod app node On-Demand, cost ceiling $50** (alerts $42/$46, hard stop $48) — applied to prod 2026-10-08 after Spot `t4g.medium` had no capacity in any AZ twice; also corrects the budget table, which had left ~$3.75 of its own rows out of its totals ([ADR-0028](docs/adr/0028-prod-app-node-on-demand-and-ceiling-50.md), `KAV-58`)
- [x] `infra/envs/staging` — second node (On-Demand by default), own VPC, own k3s, 10 GB EBS, **plus its own database server** from `infra/modules/database`; applied, verified and destroyed live; found and fixed a cross-environment database-lookup hazard and two latent roles bugs (`KAV-57`, Lab 29)
- [x] `make staging-up` / `staging-status` / `staging-down`, and staging **parks itself** after 4 idle hours (an idle-stop Lambda scales the node to zero and stops the database; it parks rather than destroys, so a parked staging costs ~$1.64/month until `staging-down`). Whole cycle proven live: build, idle park, resume in 282 s, destroy in 206 s ([ADR-0029](docs/adr/0029-staging-parks-itself-when-idle.md), `KAV-59`, Lab 30)
- [~] `release.yml` — build once, push by digest, deploy staging, smoke test, release notes; component versions bumped from the commits touching each component and tagged `<svc>-vX.Y.Z`, the product release `vX.Y.Z` named with its components' versions (ADR-0011, ADR-0013)
  - [x] **Stage one, live:** five arm64 images built once, Trivy-scanned and pushed to ECR as `sha-<short>` through a push-only OIDC role (this repo, `main` only, no stored key); a pull request pins staging to the tag (Flux pulls it, as in prod). 1 min 47 s end to end; the backup image's scan caught 20 new HIGH CVEs before publishing; the first run was refused over GitHub's immutable `sub` claim and fixed from CloudTrail ([ADR-0030](docs/adr/0030-ci-publishes-images-by-oidc-and-staging-is-pinned-by-pull-request.md), `KAV-61`, Lab 32)
  - [x] **Stage two, live:** `make staging-smoke` checks a running staging read-only (pods, no crash loop, tag, **running digest equals ECR's**, database + migration revision, REST reads) and records the pass in `deploy/promotion/passed-staging.json`; `sha-2459417` passed, and a wrong expectation failed on three counts ([ADR-0031](docs/adr/0031-staging-smoke-test-and-the-passed-staging-record.md), `KAV-62`, Lab 33). Run by hand, not by CI (that needs a second AWS role). Not covered: the model, and the backup image, which staging does not deploy and prod does not run yet, so `promote.yml` pins only the four deployed images
  - [x] **Component versions are computed and tagged:** `versions.py` reads the conventional commits since each tag; `release-prepare.yml` opens the bump pull request; `release.yml` tags `<svc>-vX.Y.Z` and `vX.Y.Z`; the baseline `v0.1.0` marks `sha-2459417`. Which file ships in which image is tested against the Dockerfiles ([ADR-0035](docs/adr/0035-computed-versions-tags-and-the-generated-change-record.md), `KAV-67`, Lab 36)
  - [x] An ECR retention rule — ECR keeps the newest 15 tagged images per repository (storage capped at about $0.71/month instead of growing by ~0.45 GB a release), and `make up` now checks first that the images prod pins still exist ([ADR-0036](docs/adr/0036-ecr-keeps-the-newest-fifteen-tagged-images.md), `KAV-68`, Lab 37)
- [x] `promote.yml` — the gate. A person dispatches it with a tag; it **refuses a digest that did not pass staging** (record, ECR digests still equal, newer than prod) and a release with a `uat` story not yet signed off in Jira (ADR-0012), then opens the pull request that pins prod; merging it is the go/no-go. A required CI check, `promotion-guard`, stops hand edits of prod's tags. Proven live: `sha-2459417` passed and was **merged into prod's pins on 2026-10-09** (PR #83; prod itself is still parked, so nothing has run yet), `sha-ffb436b` was refused ([ADR-0032](docs/adr/0032-promote-workflow-and-the-promotion-guard.md), `KAV-63`, Lab 34). Needs three Jira read-only Actions secrets
  - [ ] **Deadline 2026-11-04 (`KAV-65`):** replace the one remaining exception (CVE-2026-111943, fixed only in Go 1.27.3) by pinning an OPA built with Go 1.27.3 or later, or build OPA from source in the Dockerfile ([ADR-0033](docs/adr/0033-dated-trivy-exception-for-the-bundled-opa-binary.md), updated 2026-10-21; OPA v1.21.3 closed the first three CVEs)
  - [x] A dated, path-scoped Trivy exception for Go CVEs in the bundled OPA binary (the image checks were red on every pull request). The first three ended 2026-10-21 when OPA v1.21.3 was pinned; one new one is excepted until 2026-11-04 ([ADR-0033](docs/adr/0033-dated-trivy-exception-for-the-bundled-opa-binary.md), `KAV-64`)
  - [x] Dependabot watches all five images (the executor and backup were missing) and holds major `kubernetes` updates back ([ADR-0010](docs/adr/0010-ci-pipeline-and-supply-chain.md) amendment, `KAV-69`)
  - [x] Move to the `kubernetes` client 37 (`KAV-70`): fix the 26 mypy errors its new type hints expose, exercise the executor's restart and delete on staging, then remove the Dependabot ignore
- [x] `rollback.yml` — measured time-to-restore: about 80 s merge-to-healthy on staging, and the finding that a rollback across a database migration stalls until the migration is undone first ([ADR-0034](docs/adr/0034-rollback-workflow-and-what-a-rollback-may-go-back-to.md), `KAV-66`, Lab 35, [runbook](docs/runbooks/rollback-prod.md))
- [x] Generated change records in `docs/releases/`: `promote.yml` puts `change_record.py`'s file in the promotion pull request (components, changes, Jira keys, derived risk, staging evidence, rollback plan with the measured 80 s); the first record is backfilled for `v0.1.0` (`KAV-67`, Lab 36). `release-prepare.yml` ran live (baseline tags `v0.1.0` pushed 2026-10-10); the new `promote.yml`/`release.yml` steps first run at the next real release
  - [x] `rollback.yml` marks the record it undoes `Rolled back: yes` in the rollback pull request (`KAV-71`); "Time to restore" stays a hand-filled measurement
  - [x] A Jira Release (fix version) `Kaval X.Y.Z` per product version: `jira-sync.py release X.Y.Z` (`make jira-release`) reads the change record, creates the release and puts its issues in it. `Kaval 0.1.0` made live 2026-10-10 with its nine issues; repeatable ([ADR-0013](docs/adr/0013-component-versions-and-release-naming.md), 6; `KAV-35`, Lab 42)
- [x] `docs/learn/phase-4-aws-landing.md` — flip **Written from** to `experience` (2026-10-10: an assumed-vs-happened table from the labs, stale claims corrected, interview answers from what was built)

**Exit gate:** terminate the node by hand; it rebuilds itself from Git in under 5 minutes. ✅ **Proven and timed on prod 2026-10-08: 3 min 36 s** from `terminate-instances` to every pod `Running 1/1`, no human step (`KAV-60`, Lab 31). The phase stays `[~]` for the release pipeline lines above.

---

## Phase 5 — Chaos + proof · weeks 16–17 · `[~]`

**Posture: paused between sessions.**

*Plan agreed 2026-10-11, seven slices, one Jira story each under `KAV-10`: 1 service levels and outcomes (`KAV-77`) · 2 watch the watcher and Prometheus on staging (`KAV-78`) · 3 chaos framework and the memory experiment (`KAV-79`) · 4 crashloop and lost server (`KAV-80`) · 5 full disk and slowness, escalate with no action (`KAV-81`) · 6 people and process (`KAV-82`) · 7 close-out and Prometheus to prod (`KAV-83`). Roughly 5–7 sessions, longer than the weeks 16–17 first written. No chaos runs on prod; "handled end to end" means the right outcome per type, which for disk and slowness is an alert with no automated action.*

- [x] Switch Prometheus on in **staging**, then **prod** (`KAV-78` staging and measure — on in staging: real memory measured (services 63-105 MiB, Prometheus ~80 MiB, node 1.79-1.99 of 4 GiB) and kubelet TLS verified on k3s; **run live in production 2026-10-14** (`KAV-83` Part 3, [runbook](docs/runbooks/prod-rollout.md)): all 10 targets up, including the database server's two exporters; the server at 1,576 MB used and 2,062 MB available of 3,829 MB; services 45-81 MiB each; `KAV-76` is the umbrella) — `prometheus.enabled` in each values file, measure its real memory on staging's 4 GB node first, then promote through the normal gate; built in Phase 3 (ADR-0040, `KAV-75`), still off outside `local`. The OOM chaos type needs it on staging to be caught early
- [~] Chaos CronJobs: OOM kill · crashloop · disk fill · latency injection · node drain — **OOM kill done and proven on staging** (`KAV-79`, [ADR-0044](docs/adr/0044-chaos-with-our-own-jobs-and-a-bounded-memory-fix.md), [Lab 46](docs/labs/lab-46-chaos-and-the-memory-game-day.md), [post-mortem](docs/postmortems/2026-10-11-oom.md): noticed in 81 s, fixed after approval, held); **bad release and lost server done and proven on staging** (`KAV-80`, [ADR-0045](docs/adr/0045-rolling-back-a-bad-release-and-flux-versus-direct-fixes.md), [ADR-0046](docs/adr/0046-losing-the-app-server-and-how-we-test-it.md), [Lab 47](docs/labs/lab-47-chaos-bad-release-and-lost-server.md), post-mortems [bad release](docs/postmortems/2026-10-11-badrelease.md): noticed in 66 s, rolled back on approval, held; [lost server](docs/postmortems/2026-10-12-node.md): replaced by itself, everything back in about 2 min 10 s, data intact); **disk full and slowness done and proven on staging** (`KAV-81`, [ADR-0047](docs/adr/0047-incidents-that-are-for-people-disk-full-and-slowness.md), [Lab 48](docs/labs/lab-48-chaos-disk-full-and-a-slow-service.md), post-mortems [slow service](docs/postmortems/2026-10-12-slow.md) (three runs: two real defects found, third passed) and [disk](docs/postmortems/2026-10-12-disk.md): incident of its own within about 35 s, a person would be told, no automatic proposal, a hand-filed restart refused 409, nothing executed). Not seen live: a real Slack post (production has no incident to announce, and a pretend one in its real database was ruled out; its notifier runs without errors there after the permissions fix) and the production disk line under load (nothing is injected into production, by plan). **Exit review 2026-10-16: all five types (memory, bad release, lost server, disk, slowness) have a written hypothesis, a post-mortem and a held outcome on staging, so the gate's first half is met; what remains is production-only and is tracked as `KAV-90`**
- [x] Blast-radius containment — chaos confined to the demo namespace by a separate runner identity (its only write grant is there), off in prod (the prod render holds no chaos object, tested), a switch every deploy resets, a clock and `make chaos-stop`; proven with `kubectl auth can-i` on staging (`KAV-79`, ADR-0044). A separate `kaval-chaos` namespace was dropped: see the ADR
- [~] MTTR measurement and dashboard — `outcome` is now written by the executor five minutes after a fix and `make mttr` reports it by stage (`KAV-77`, [ADR-0041](docs/adr/0041-service-levels-and-recording-whether-a-fix-held.md)); first real numbers from the oom game day (`KAV-79`, `make chaos-mttr`: detect 81 s, machine 158 s, wall-clock 431 s); the chart is drawn (2026-10-13, `KAV-83`: [mttr.svg](docs/architecture/mttr.svg), seven recorded runs, stages tested to add up; the seventh, 2026-10-15, is the first where Kaval's own model did the diagnosing: detect 60 s, diagnose 25 s, machine 103 s, wall-clock about 195 s, against 158 s and 431 s for the first); production's own numbers come from `make mttr` once it has a real fix (`KAV-90`)
- [x] Database server monitoring — `postgres_exporter` + `node_exporter` → Prometheus, disk-space alert; SSM Patch Manager schedule; connection and failed-auth logging reviewed (ADR-0008) — **built 2026-10-13** (`KAV-82`, [ADR-0050](docs/adr/0050-monitoring-and-patching-the-database-server.md), Lab 49): two exporters (64 MiB each, app node only), a read-only monitoring role, the database server's disk through the ADR-0047 path (85 %), `KavalDatabaseDown` and `KavalDatabaseMonitorDown`, weekly no-reboot patching, connection logging with a log-size cap, all applied by SSM State Manager (the server ignores user_data after creation). **Proven live on staging 2026-10-13** (the association ran, both exporters answered, Prometheus showed them up, a forced disk alert read "the database server's disk is nearly full" with nothing proposed, ~7 MiB per exporter). **Production: seen live 2026-10-14** (`KAV-83`, Lab 50): both exporters and the app server's Prometheus `up` there
- [x] Runbooks written for each failure class *(also the agent's RAG corpus)* — `pod-oomkilled.md` (`KAV-79`), `pod-crashloop-after-release.md` and `node-lost.md` (`KAV-80`), `disk-pressure.md` and `latency-spike.md` (`KAV-81`) written; the rest with their experiments, closed in `KAV-83`. **All written** by the 2026-10-16 review: also `approval-unanswered`, `backup-missing`, `restore-drill`, `diagnosis-not-happening`, `still-alive-alarm`, `prod-rollout` and `terraform-state`; `cost-spike.md` waits for Phase 6
- *Planning-standard additions (2026-10-11), each a checkbox below:*
- [x] Chaos tooling decision, as an ADR — build our own CronJobs or adopt Chaos Mesh / LitmusChaos (what most teams use); chaos runs on **staging first**, and whether prod ever gets a supervised game day is decided explicitly — [ADR-0044](docs/adr/0044-chaos-with-our-own-jobs-and-a-bounded-memory-fix.md): our own jobs; no chaos on prod in Phase 5, with the minimum conditions written down for ever asking (`KAV-79`)
- [x] Each chaos run is a game day: a written hypothesis, the steady state it is measured against, abort criteria with a kill switch, and a short blameless post-mortem in `docs/` — [template](chaos/game-day-template.md), first [post-mortem](docs/postmortems/2026-10-11-oom.md) (`KAV-79`)
- [x] Define the service levels Kaval is held to — time to detect, time to restore, and what counts as failing — before charting MTTR — [service levels](docs/architecture/service-levels.md), provisional until the first game day (`KAV-77`)
- [x] **Watch the watcher:** a heartbeat / dead-man's-switch alert if the collector, agent or Prometheus stops; every Kaval service exposes `/metrics`; one dashboard for Kaval's own health — built (`KAV-78`, [ADR-0042](docs/adr/0042-watching-the-watcher.md), [Lab 45](docs/labs/lab-45-watching-the-watcher.md), [runbook](docs/runbooks/kaval-stopped-reporting.md)): health page on :9100 in every service, three promtool-tested rules including an always-on watchdog, a Slack message straight from the signals, a Grafana dashboard in git. Proven live on staging (stalled loop, missing pod, Prometheus down; 32 quiet minutes with two restarts). The "still alive" alarm is built (`KAV-83` Part 2, [ADR-0053](docs/adr/0053-the-still-alive-alarm-lives-in-aws.md), [runbook](docs/runbooks/still-alive-alarm.md)): the node puts one CloudWatch datapoint every two minutes, only while the poller is finishing passes, and one alarm e-mails when they stop for 25 minutes while the app-server group wants a server (a parked production is silent by itself: it wants none). AWS-only, no server, no stored secret, about $0.72 a month at most. It shares the region's failure points, which an outside service would not (ADR-0053). Proven live on staging 2026-10-13 (datapoints, rings when the poller cannot finish passes, quiet for 5.5 hours parked) and applied to production 2026-10-14: the pretend ring sent two alarm e-mails, datapoints arrived every two minutes while production ran and the alarm stayed `OK` for about 45 minutes, then it was parked and quiet (Lab 50 Part 2 and Part 3)
- [x] Alertmanager as a signal source (the pushed half of Prometheus): adopt it or record why not, and the "Prometheus is down" case raised as an incident of its own — recorded as *not adopted yet* ([ADR-0043](docs/adr/0043-alertmanager-not-adopted-yet.md)); "Prometheus is down" is raised by the poller (`kaval_prometheus_unreachable`), proven live on staging (`KAV-78`)
- [x] Flux versus direct fixes, as an ADR — direct fixes only where Flux does not own the object (the executor cannot reach anything Flux owns); a fix to a Flux-owned object is a change in git by a person. An action that writes to git is **not built** and is deferred until Kaval looks after a workload Flux owns ([ADR-0045](docs/adr/0045-rolling-back-a-bad-release-and-flux-versus-direct-fixes.md), `KAV-80`)
- [x] An OOM kill is its own cause — the collector reads the pod's last exit reason on a `BackOff`, so a real out-of-memory kill is `oom_killed`, not `crashloop` (`KAV-80`, seen live on staging 2026-10-12)
- [x] Causes that are for a person (a full disk, a slow service, Kaval's own health) are enforced in four places (agent, policy, executor, gateway) and get their own incident lane in the correlator, so a harmless event can never rename them into something a fix may be proposed for (`KAV-81`, [ADR-0047](docs/adr/0047-incidents-that-are-for-people-disk-full-and-slowness.md); found by the slow-service game day)
- [x] The two copies of an environment's settings (`deploy/environments/<env>/values.yaml` and the inline copy in the HelmRelease Flux deploys) are checked equal by a test (`KAV-81`; the first slow-service run found a staging setting that never reached the cluster)
- [x] Disk and slowness in **prod**: Prometheus is not there yet, so prod cannot see either until it is; choose real probe targets (Kaval's own gateway first) and keep the disk line at 0.85 (`KAV-83`) — **settings written 2026-10-13** (Prometheus, the database exporters and a timed request to its own gateway are on in production's two settings files, disk line 0.85, tested); **seen live 2026-10-14**: production ran the promoted version with Prometheus, the app server's container metrics, the database server's host and Postgres exporters and the timed gateway request all `up`. The disk line itself was not exercised (no chaos on production, by plan)
- [x] A disk *trend* (full within N hours) — decided 2026-10-13: not built. The app server's disk resets with every bring-up and the 85 % line leaves 3 GB; the database volume holds almost nothing. Trigger written down: the database data volume past half full ([ADR-0051](docs/adr/0051-closing-phase-5-readiness-checks-prod-prometheus-and-the-disk-trend.md), `KAV-83`)
- [x] Readiness probes on the loop workers (agent, collector, executor, and the Prometheus poller): without one a bad release of one of them takes the old version down as well — found by the bad-release game day — **built 2026-10-13** (`KAV-83`, [ADR-0051](docs/adr/0051-closing-phase-5-readiness-checks-prod-prometheus-and-the-disk-trend.md), [Lab 50](docs/labs/lab-50-closing-phase-5.md)): one shared check on the health page, readiness only, tested in every environment. **Proven live on staging 2026-10-13:** released as a program that starts and dies 20 seconds later, the old copy stayed `1/1 Running` for the whole run with the check, and was removed within 10 to 40 seconds without it. Production gets it when it is next promoted
- [x] Teach the diagnosis model which fixes exist, measured by `make evals` before and after (`KAV-84`, [ADR-0054](docs/adr/0054-telling-the-model-which-fixes-exist.md), [Lab 51](docs/labs/lab-51-teaching-the-model-the-fixes.md); found by the oom game day: cause named right 3 of 3, no action proposed 3 of 3) — **measured 2026-10-14, three runs on the dev server:** the right fix aimed at the right workload went from **0 of 8 to 8 of 8** every time, no action where Kaval has none stayed 6 of 6, 0 of 14 proposed actions off the supported list, no never-class action. What it took: the list in the system prompt changed nothing (0 of 40) because `actions` was an optional field the 1B model always left out; the fix then had to come last in the message, be the only one shown (chosen from the incident's cause, not by the model), and be forced by the reply format itself (15 of 15, against 3 of 15 when the model could decline). So the model no longer chooses the action; a person still approves it. Side effect: stated confidence rose (mean 0.76 to 0.88)
- [x] **Kaval diagnoses new incidents by itself in staging and prod** (`KAV-87`) — found while planning `KAV-84`: nothing in the cluster calls the language model today (the agent only groups signals; staging proposals are filed by hand), so the better prompt reaches only people who run the command. **Built 2026-10-14, proven on staging 2026-10-15** ([ADR-0055](docs/adr/0055-the-model-runs-in-the-cluster-and-diagnoses-new-incidents.md), [Lab 52](docs/labs/lab-52-the-model-in-the-cluster-and-the-diagnosis-worker.md)): the model runs as its own pod on the app node (memory-limited to 1.5 GiB, unloaded when idle, files checked against a pinned id), a separate diagnosis worker files one proposal per new incident (a speed limit, a pause after a model failure, nothing for incidents meant for a person), two alerts (an incident waiting over 15 minutes; no worker at all), runbooks shipped in the image. On staging a new incident got exactly one proposal with nobody running a command, a full-disk incident got none, the model switched off rang the backlog alarm while the correlator and executor carried on, and the model back cleared it; the model adds about 1.13 GB while loaded (780 MB left free at the worst moment) and no extra processor credits were charged. The live run found a missing read permission and fixed it (PR #170). **Switched on in prod 2026-10-16** after the build passed staging and was promoted, with the memory measured again on the real node: 2,007 MB free idle, **872 MB free at the worst moment** (estimate 850), 2,045 MB free six minutes after the last call, one call 33.9 s, nothing restarted, $0 recurring and $0 surplus processor credits (ADR-0055, "Measured on production"). Also fixed on the way: the staging smoke test now compares bytes, not tag names (PR #173)
- [x] **The model's stated confidence goes down when the evidence is thin** (`KAV-88`) — the two sparse golden cases have failed since Lab 13 and `KAV-84` pushed confidence up (0.76 to 0.88). **Built and measured 2026-10-16** ([ADR-0056](docs/adr/0056-the-stored-confidence-follows-the-evidence.md), [Lab 53](docs/labs/lab-53-confidence-that-follows-the-evidence.md)): asking the model again was already known not to work, so the number stored is limited by a rule over the incident's own signals (one observation at most 0.5, several of one kind at most 0.7, two kinds untouched; only ever lowers; cost and unused-disk incidents untouched), applied in the one place every proposal is written, with a counter and a log line saying when it bites. On the real model: 20 of 20 cases pass (18 before), the two sparse ones now store 0.50 (the model still said 0.95), exactly those two were lowered, nothing else changed (right fix 8 of 8, no action 6 of 6). The 20 of 20 is partly by construction; **Staging check done the same day**: it found the first rule too blunt for a real memory incident (17 kill events and no other kind stored 0.70 for the model's 0.95), fixed with a per-cause exemption for the signal that names the cause, then re-run on the new build: stored 0.80 unchanged, fix held, smoke check 7 of 7. **Promoted to production's pin 2026-10-16** (`sha-06b64ba`, PR #182); production is parked, so its first live proof is when it is next brought up. Jira criterion 1 ("find which part of the prompt pushes confidence up") was reworded with a recorded reason: that was not isolated, the number is limited outside the model instead
- [~] Approvals that nobody answers: an expiry and an escalation (a reminder, then a second person or channel), recorded in an ADR — **built 2026-10-13** (`KAV-82`, [ADR-0048](docs/adr/0048-approvals-nobody-answers-expiry-and-escalation.md), Lab 49): reminder 15 min, louder message 30 min, expiry 4 h (staging 3/7/15), refusal enforced where the decision is written, the executor refuses an approval older than 60 min, people-only faults escalate after 2 h. Found and fixed a real bug on the way (the gateway's database role could not mark a Slack message, so a real post would repeat every 30 s). **Proven live on staging 2026-10-13:** the louder message at 13 min, then an approval 16 min 40 s after filing was refused with 409. **Not yet done:** a real Slack post in prod (`KAV-90`)
- [~] Alert if the nightly database dump is missing and a restore drill on a schedule, with the recovery time and acceptable data loss written down — **built 2026-10-13** (`KAV-82`, [ADR-0049](docs/adr/0049-backups-watched-and-restores-drilled.md), Lab 49): a daily GitHub check that knows a parked production owes no dump, a read-only role for it, `make restore-drill` with a dated record (first drill passed, production held no data yet), [disaster-recovery.md](docs/architecture/disaster-recovery.md) (RPO 24 h, RTO 30 min; two gaps found: Terraform state was only on the laptop, closed by `KAV-86` on 2026-10-16; the dump skips parked nights, still open). **Proven live 2026-10-13:** the role is applied to production (2 added, nothing else), the two repository variables are set, and the daily check's first run logged in through the role and passed (`OK`, newest dump 2026-10-10). The 19:30 UTC timer firing on its own is still unseen (`KAV-90`)
- [x] Re-measure the 4 GB node's headroom with everything on before calling the phase done — **measured 2026-10-14 on production's own server**: 1,576 MB used and 2,062 MB available of 3,829 MB (52 percent) with all five services, Prometheus and the exporters up (staging, chaos installed but off: 1,407 used, 2,231 available); the model is not hosted on this server
- [x] `docs/learn/phase-5-chaos-and-proof.md` — flip **Written from** to `experience` (2026-10-14)
- [x] **A new version with a database change starts cleanly on an old production database** (`KAV-85`, [ADR-0057](docs/adr/0057-the-database-is-brought-up-to-date-before-a-new-version-starts.md), [Lab 54](docs/labs/lab-54-a-new-version-on-an-old-database.md)) — found live 2026-10-14, the first production run over a database two migrations behind: the install waited for the executor, the executor needed the migration, and the migration waited for the install; and the table permissions were applied before the migration made its table. Now the migration runs before the install and the permissions are handed out again once the release is Ready; CI fails a migration that removes anything or a table with no `GRANT`. Reproduced and fixed on staging 2026-10-16 (server replaced over a database two versions behind: Ready by itself, smoke 7 of 7); production runs it the first time it is brought up
- [x] **Terraform's record of what exists lives off the laptop**, versioned and locked (`KAV-86`, [ADR-0058](docs/adr/0058-terraforms-record-lives-in-a-private-versioned-bucket.md), [Lab 55](docs/labs/lab-55-terraform-state-off-the-laptop.md), [runbook](docs/runbooks/terraform-state.md)) — **built and proven 2026-10-16**: one private, versioned, encrypted bucket with S3's own lock holds prod, staging, dev and its own record (copies equal the old files; staging No changes; prod shows the same 2 launch-template changes as before the move). A second run was refused while the first held the lock, a fresh clone planned staging with No changes, and a record damaged on purpose was restored from an earlier version. Under $0.01 a month. A CI test keeps every environment on the remote bucket and the bucket's name out of git. A new-laptop recovery also needs the gitignored settings files, written into the runbook

**Exit gate:** five chaos types handled end-to-end, MTTR charted.

---

## Phase 6 — FinOps domain · weeks 18–20 · `[ ]`

**Posture: always-on from here.** Cost Explorer needs real days of data.

- [~] Cost Explorer + CUR ingestion — the Cost Explorer half is **built and tested 2026-10-18** (`KAV-92`, [ADR-0060](docs/adr/0060-the-cost-reader-the-server-asks-aws-and-the-cluster-never-holds-the-login.md), [Lab 57](docs/labs/lab-57-the-cost-reader.md)): the server's daily job reads the bill as a read-only role and leaves it in one ConfigMap; a pod with no AWS login turns settled days into `daily_cost` signals; tested on a recorded real bill, capped at 60 requests a month, three alarms. **Proven live on staging 2026-10-18** (65 rows from one real request; a pull held at the cap was refused with no request, the alarm number went to 1 and came back by itself; the second identical bill added nothing; 86 MiB); **production, 2026-10-21 (`KAV-98`):** the reader took the bill in (1 of the 60 calls), alarm number 0. The Cost and Usage Report (line items in S3) is not built: Cost Explorer's daily figures are enough to find spikes, line items are what waste detection (`KAV-94`) may need, and the decision is made there
- [~] Tagging strategy and enforcement — **built and tested 2026-10-18** (`KAV-93`, [ADR-0061](docs/adr/0061-every-resource-carries-three-tags-and-the-bill-is-split-by-them.md), [Lab 58](docs/labs/lab-58-tags-and-the-showback-report.md)): three required labels (`Project`, `Env`, `ManagedBy`), a test on every pull request that fails code which would create an unlabelled resource (negative cases included), `make tag-scan` for the live account (found 12 of Kaval's 103 resources unlabelled before the fix: the server the Auto Scaling group launches, its disk, 10 pre-stop snapshots) and `make cost-showback`. **Done live 2026-10-18:** staging applied (the group and launch settings now label a new server and its disk), `Env` and `ManagedBy` switched on as billing labels with the backfill requested from 1 August, the 10 old snapshots and the staging disk labelled (scan: 12 findings to 1, the server that was already running). **Proven live 2026-10-19 and 2026-10-21:** a freshly launched staging server and its disk carried all three labels (`make tag-scan`: 0 of 107 missing, 0 of 115 on the 21st), and the first real `make cost-showback` ran ($9.19 for October so far, 34 % with no `Env` label: the account is shared with another project, and public addresses and the cost tool's own calls cannot carry a label; `docs/cost/showback-2026-10.md`). **Production, 2026-10-21 (`KAV-98`):** after the bring-up `make tag-scan` read 0 missing of 124 (66 in production)
- [~] Waste detection: idle nodes, orphaned volumes, unused snapshots — **built and tested 2026-10-18** (`KAV-94`, [ADR-0062](docs/adr/0062-waste-is-found-daily-by-a-read-only-scan-and-reported-to-a-person.md), [Lab 59](docs/labs/lab-59-the-waste-scan.md), runbooks [waste-found](docs/runbooks/waste-found.md) and [waste-scan-not-running](docs/runbooks/waste-scan-not-running.md)): a read-only function on its own daily schedule finds a disk or address attached to nothing on two scans in a row, backup copies past 30 days (pre-stop) or 90 (hand-made) and never the newest of a disk, staging/dev servers running over 12 hours (production never), and Kaval resources missing their labels; the server copies the result into the cluster, the cost reader's pod rebuilds each finding from a whitelist and writes one signal, each becomes an incident for a person (no model, no proposal, policy says never). Two alarms (failing 4 hours, stale 36 hours). "Idle by CPU" was rejected: staging's real CPU averages 7–12 % when up. $0 a month. **Proven live on staging 2026-10-18:** applied and switched on, the first scan clean, then a planted 1 GiB disk was found on the second scan, became an incident, and a clean scan followed its removal (Lab 59 Parts 5–6). **The failure proof (Part 7) passed the same day:** the scan's login was made unable to look, the hand-over said `failed` / `describe_failed`, the reader's alarm number went to 1, and back to 0 after the restore. The unlabelled finding and the "another project is left alone" check were also seen live. **Production, 2026-10-21 (`KAV-98`):** applied and switched on, the first scan run by hand came back clean (0 findings) and the reader took it in (alarm numbers 0); the scheduled 03:15 UTC firing has not been watched. **Still to do:** the server-overstay live example (needs staging kept awake past 12 hours)
- [x] Cost proposals through the same approve/execute path — built by `KAV-95` (above): the waste scan's findings become proposals made by code, a person approves each risky step, the executor hands the order to the separate worker, and the worker's answer comes back as a signal. Closed here (`KAV-97`) because the line was an umbrella that `KAV-95` completed; what it did not have was the accounting, which is the next line
- [x] Hard-stop Lambda wired to the live ASG — **proven on staging 2026-10-21 and 2026-10-22, closed 2026-10-22** (`KAV-96`, [ADR-0064](docs/adr/0064-the-spending-brake-names-every-server-group-and-is-proven-on-staging.md), [Lab 62](docs/labs/lab-62-the-spending-brake.md), [runbook](docs/runbooks/budget-hard-stop-fired.md)): it names production's and staging's server groups, has 12 tests and a guard that the ceiling's numbers agree everywhere; the real alert path set it off on staging (the group went 1 to 0, the database was stopped), which found that its pre-stop snapshot was refused (a missing permission, also true of the nightly 02:00 IST run), fixed and re-tested. Not run, by choice: lowering the real budget so AWS Budgets itself sets it off; the first real $48 crossing is that link's live proof
- *Planning-standard additions (2026-10-11), each a checkbox below:*
- [~] A read-only role for the cost reader, separate from the executor's role; the executor's cost actions get an explicit allow-list and a permission boundary — the read-only role is **built 2026-10-18** (`KAV-92`: `ce:GetCostAndUsage` and nothing else, only that environment's server may borrow it, the cluster holds no AWS login); the executor's allow-list and permission boundary come with the cost proposals (`KAV-95`)
- [x] The safe way to remove waste, as practised in industry: tag it, stop it, wait a grace period, snapshot, only then delete; every action reversible or snapshotted first, with a dry-run preview in the proposal — **built and tested 2026-10-19; proven live on staging 2026-10-19 to 2026-10-21** (`KAV-95`, [ADR-0063](docs/adr/0063-waste-is-removed-in-steps-by-a-separate-worker-and-only-with-approval.md), [Lab 60](docs/labs/lab-60-safe-removal.md)): two approvals per item with a wait between (label + backup, then delete), a separate worker with its own narrow login that re-checks AWS itself, the proposal made by code and never by the model, first slice = disks attached to nothing and backup copies past retention. Built: the worker (own narrow login, IAM refuses to delete an unmarked disk), the planner, the executor's hand-over, the reader of the worker's answers, the chart switch and the unanswered-order alarm, the approval message with its preview and undo, and a whole-ladder test with a pretend AWS (it found and fixed a bug before anything ran live). **Proven live on staging (Lab 60 Parts 6–7, each step on its own go):** back up and mark, delete after approval, rebuild a disk from the backup, and five refusals (another project's disk, a Terraform-owned one, an attached one, one still in its wait, one with no backup). Found live and fixed: the planner could not read approvals (a database grant), and it kept trusting a vanished disk for up to 36 hours (the scan now leaves a record). An unanswered proposal that expires is now asked again after 24 hours (fixed 2026-10-21, running on staging as `sha-3ab86d1`). **Closed 2026-10-21.** Covered by tests only, not fired live: the other-environment refusal, the six-hour unanswered-order alarm, and deleting an old backup copy (a Kaval-made copy waits 30 days). **On in production since 2026-10-21** (`KAV-98`, PR #225, wait 168 hours); no finding yet, so the production worker has never been used
- [~] Tag policy enforced (required keys on every resource) and a show-back report by service/environment — enforcement and the report are built and the first real report ran (`KAV-93`, above). A daily scheduled check with an alert for servers, disks, snapshots and addresses is built (`KAV-94`, the `resource_unlabelled` finding); `make tag-scan` stays for every other resource type
- [~] Savings tracked: proposed vs realised, so the phase can show money actually saved — **built and tested 2026-10-22** (`KAV-97`, [ADR-0065](docs/adr/0065-savings-count-only-what-the-worker-confirmed-gone.md), [Lab 63](docs/labs/lab-63-savings-tracking-and-the-phase-6-exit-test.md)): the planner stores each delete's promised monthly saving as a number, and `make savings-report` / `make staging-savings` sort every proposed deletion into confirmed, approved, waiting, declined or expired and total them; only a deletion a person approved **and** the worker confirmed counts as saved, and the report says it is an estimate priced from the scan's table, not read off the AWS bill. 22 tests. **Still to do:** the live run on staging (the exit test, Lab 63 Part 4)
- [x] Re-do the cost model for "always-on" (the $50 ceiling was set for a parked system): a new ADR and budget-plan update **before** switching the posture (`KAV-91`, [ADR-0059](docs/adr/0059-always-on-cost-model-and-measuring-usage-before-credits.md): about $39 a month expected; found that the budget counts usage after credits, so its alerts would not fire, fixed on the live budget the same day; credits cover about three and a half months of always-on)
- [x] Compare against AWS Cost Anomaly Detection (what most teams switch on first) and record what Kaval adds beyond it (`KAV-91`, ADR-0059: it finds changes, Kaval finds waste that never changes, then proposes and acts through approval; turning it on as a second opinion happens in `KAV-92`)
- [~] **Production bring-up for Phase 6** — **brought up, verified and parked again 2026-10-21; the scheduled-timer checks (`KAV-90`) still open** (`KAV-98`, [Lab 61](docs/labs/lab-61-production-bring-up-for-phase-6.md), PRs #222-#225): the cost reader (`KAV-92`), the tag fixes (`KAV-93`) and the waste scan (`KAV-94`: its function, schedule and parameter, the server's right to read that parameter, `costReader.wasteScan.enabled` in production's values) and the removal worker (`KAV-95`: its module, parameters and `removal.enabled` in production's values) go live in production at its next bring-up, together with the `KAV-90` production-only checks; plan shown first and applied on Roshan's go; `make tag-scan` must then read 0 and the first waste scan must run clean
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
- *Planning-standard additions (2026-10-11), each a checkbox below:*
- [ ] A time-to-live on the EKS cluster so it cannot be left running ($73/month for the control plane alone): an automatic destroy, like staging's idle stop
- [ ] Network policies and Pod Security Standards applied, and the chart's behaviour under them checked (EKS enforces more than k3s)
- [ ] Observability parity: Prometheus and the poller run from the same chart by values only; the Kubernetes version and upgrade policy are written down
- [ ] Terraform plan reviewed and recorded before every apply; teardown verified by a tag-based sweep, not by memory
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
- [ ] Switch the promotion gate to GitHub Environments with required reviewers *(possible now: the repo is public)*
- *Planning-standard additions (2026-10-11), each a checkbox below:*
- [ ] Supply chain, as in industry: an SBOM per image, signed images (cosign keyless), build provenance attestations, and signatures **verified before deploy** (an admission policy), not just produced
- [ ] A threat model (STRIDE) for the whole system, including the agent reading untrusted text; a **prompt-injection test set** added to the evals (OWASP Top 10 for LLM applications)
- [ ] Public-repo hygiene: `SECURITY.md` (how to report a vulnerability), `CODEOWNERS`, OpenSSF Scorecard checked, dependency updates reviewed (Dependabot already on), secret rotation schedule documented
- [ ] IAM least-privilege review with IAM Access Analyzer, kept as evidence
- [ ] Disaster recovery stated and drilled: recovery time and acceptable data loss for each store, one full-restore drill recorded (Terraform's state gap closed by `KAV-86`, 2026-10-16)
- [ ] CI shows each pull request's Terraform plan, through a read-only role on the state (`KAV-89`; follows `KAV-86`)
- [ ] Ownership page: who is on call for what, how an incident is declared, the escalation path (even if it is one person, written down)
- [x] Protect `main`: require the CI checks to pass before a pull request merges — done 2026-09-30 when the repo went public early; found enforced and the docs still saying otherwise on 2026-10-08. All ten CI checks are required (`helm`, `image executor`, `image backup` added 2026-10-08, `KAV-62`); `enforce_admins` stays off
- [x] `docs/learn/release-engineering.md` — flip **Written from** to `experience` (2026-10-10: an assumed-vs-happened table from the labs, stale claims corrected, interview answers from what was built)
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
- *Planning-standard additions (2026-10-11), each a checkbox below:*
- [ ] Approvals are privileged actions: a second factor (biometric) on the approve button, tokens in the device's secure store, and the same expiry and escalation rules as Slack
- [ ] Distribution through an internal test track only (TestFlight / Play internal testing), with a stated update and revocation path
- [ ] Push reliability measured: what happens if the push never arrives (fall back to Slack)
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

## Planning standard — how every piece of work is planned

Added 2026-10-11 after Prometheus was built, proven locally and then found to have no rollout
planned. Every piece of work is planned the way a real company would, not only built. The
checklist lives in [CLAUDE.md](CLAUDE.md#planning-standard); each future phase above carries the
lines it produced, marked *Planning-standard additions*.

---

## Definition of Done — every story

Documentation is a merge gate, not willpower.

- [ ] Code merged, CI green
- [ ] Lab doc in `docs/labs/` — reproducible by a stranger from zero
- [ ] ADR written *if a decision was made*
- [ ] Journal entry appended
- [ ] Cost impact noted in `docs/cost/`
- [ ] `docs/architecture/architecture.toml` updated if components changed
- [ ] **Deployed to staging and verified before prod** — never straight to prod. A capability built but left off in staging/prod is not finished: it gets a roadmap line and a Jira story for the rollout the same day (the Prometheus lesson, 2026-10-11)
