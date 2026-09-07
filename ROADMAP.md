# Roadmap

Nine phases, ~28 weeks at 4–6 hrs/week, sharing evenings with AI-103 study.

Every phase ends in something demonstrable. Every phase can be abandoned mid-way and resumed
without lost state. **Stopping after Phase 6 still yields a strong portfolio piece** — Phases 7–9
are additive, not load-bearing.

Legend: `[ ]` not started · `[~]` in progress · `[x]` done

---

## Phase 0 — Foundations · weeks 1–2 · `[~]`

Guardrails before anything that can cost money.

- [x] Repository skeleton and folder layout
- [x] `CLAUDE.md` — conventions for Claude Code in this repo
- [x] `docs/00-start-here.md`, ADR process established
- [x] ADR-0001 record decisions · ADR-0002 k3s + EKS · ADR-0003 region
- [x] `.gitignore`, `.env.example`, gitleaks pre-commit hook
- [ ] `git init` + first commit
- [ ] Jira Cloud free tier, project `KAV`, 9 epics
- [ ] **Fix AWS credentials** — new IAM user, MFA, named profile *(currently expired)*
- [ ] `infra/modules/budget` — alerts at $18/$22, hard-stop Lambda at $24
- [ ] `terraform apply` the budget module and fire a test alert

**Exit gate:** `terraform apply` has created only a budget alarm, and it demonstrably fires.

---

## Phase 1 — Local first · weeks 3–5 · `[ ]`

Prove the loop on the laptop. AWS still costs $0.

- [ ] `docker-compose` — Ollama + Gemma 3 1B, Postgres + pgvector, gateway
- [ ] Synthetic signal generator (fake pod crashes, cost spikes)
- [ ] Data model migrations: signal · incident · proposal · action · decision · execution · outcome
- [ ] Gateway REST skeleton, health checks, OpenAPI

**Exit gate:** a fake incident flows end-to-end and lands in the database.

---

## Phase 2 — The agent loop · weeks 6–9 · `[ ]`

The hard, interesting part. Still no AWS.

- [ ] Signal correlation → incident fingerprinting
- [ ] Context builder: runbook RAG (pgvector) + past incidents + recent changes
- [ ] JSON-schema-enforced proposal output — the model must not ramble
- [ ] Policy engine: `auto` / `ask` / `never`, blast-radius classification
- [ ] Eval harness + 20 golden incidents
- [ ] Bedrock escalation path for low-confidence cases

**Exit gate:** 20 synthetic incidents produce valid, sane proposals; evals pass.

---

## Phase 3 — Kubernetes local · weeks 10–12 · `[ ]`

- [ ] k3d cluster on the laptop
- [ ] Helm umbrella chart, `local` values
- [ ] Real K8s events + Prometheus as signal sources
- [ ] Executor with scoped RBAC — the privilege split made real
- [ ] `arm64` multi-arch image builds *(Graviton is coming in Phase 4)*

**Exit gate:** kill a pod locally → agent proposes → you approve → executor fixes it.

---

## Phase 4 — AWS landing · weeks 13–15 · `[ ]`

First real spend. **Posture: paused between sessions** (`make down`).

- [ ] Terraform: network, spot node, ECR, IAM
- [ ] k3s bootstrap via cloud-init
- [ ] Flux GitOps reconciliation
- [ ] Cloudflare Tunnel — no ALB, no NAT Gateway
- [ ] Postgres PV on EBS + nightly dump to S3
- [ ] `make up` / `make down`

**Exit gate:** terminate the node by hand; it rebuilds itself from Git in under 5 minutes.

---

## Phase 5 — Mobile app · weeks 16–19 · `[ ]`

**Posture: paused between sessions.**

- [ ] Expo app scaffold, TypeScript, navigation
- [ ] Cognito auth
- [ ] Screens: Pulse · Inbox · Detail · Timeline · Ask · Settings
- [ ] Push notifications via Expo
- [ ] Approve / deny round trip

**Exit gate:** phone buzzes for a real incident and you approve it from bed.

---

## Phase 6 — Chaos + proof · weeks 20–21 · `[ ]`

**Posture: paused between sessions.**

- [ ] Chaos CronJobs: OOM kill · crashloop · disk fill · latency injection · node drain
- [ ] Blast-radius containment — chaos confined to a labelled namespace
- [ ] MTTR measurement and dashboard
- [ ] Runbooks written for each failure class *(also the agent's RAG corpus)*

**Exit gate:** five chaos types handled end-to-end, MTTR charted.

---

## Phase 7 — FinOps domain · weeks 22–24 · `[ ]`

**Posture: always-on from here.** Cost Explorer needs real days of data.

- [ ] Cost Explorer + CUR ingestion
- [ ] Tagging strategy and enforcement
- [ ] Waste detection: idle nodes, orphaned volumes, unused snapshots
- [ ] Cost proposals through the same approve/execute path
- [ ] Hard-stop Lambda wired to the live ASG

**Exit gate:** the agent finds real waste in your own account and you approve the fix.

---

## Phase 8 — EKS chapter · week 25 · `[ ]`

- [ ] `infra/envs/lab` — real EKS via Terraform
- [ ] IRSA for the executor — no static keys
- [ ] AWS Load Balancer Controller
- [ ] Deploy the **identical** Helm chart, unmodified
- [ ] Screenshot and record everything
- [ ] `terraform destroy`, verified clean 24 h later

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

**Exit gate:** repo public, video recorded, bullets written.

---

## Definition of Done — every story

Documentation is a merge gate, not willpower.

- [ ] Code merged, CI green
- [ ] Lab doc in `docs/labs/` — reproducible by a stranger from zero
- [ ] ADR written *if a decision was made*
- [ ] Journal entry appended
- [ ] Cost impact noted in `docs/cost/`
