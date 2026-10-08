# CLAUDE.md

Guidance for Claude Code working in the Kaval repository.

## What this project is

An autonomous operations agent with human-in-the-loop approval. It watches a Kubernetes cluster
and an AWS bill, diagnoses issues with a self-hosted Gemma 3 1B, proposes remediations, and
executes them only after a human approves from a mobile app.

This is a **learning project built to be documented**. Roshan is a Release Manager moving toward
an AI platform role. Two consequences that override normal defaults:

- **Explain, don't just do.** When you make a non-obvious choice, say why. The reasoning is the
  product as much as the code is.
- **Every session produces documentation.** See "Definition of Done" below. This is not optional
  polish — the docs become a course.

The full plan lives outside this repo, in Roshan's local Claude Code plan files.

---

## Non-negotiable constraints

### 1. Cost ceiling is $50/month

Raised from $25 to $40 on 2026-09-26 by [ADR-0008](docs/adr/0008-production-database-on-its-own-server.md)
(the production database moved to its own server), then to $50 on 2026-10-08 by
[ADR-0028](docs/adr/0028-prod-app-node-on-demand-and-ceiling-50.md) (the app node went On-Demand
after Spot capacity ran out twice). Alerts at $42 / $46, hard stop at $48.

Before proposing *any* AWS resource, state its monthly cost. If a change adds recurring spend,
say so explicitly and update `docs/cost/budget-plan.md`.

**Never introduce these without an explicit conversation:**

| Resource | Cost | Use instead |
|---|---|---|
| NAT Gateway | $32/mo | Public subnet + security groups |
| Application Load Balancer | $18/mo | Cloudflare Tunnel |
| EKS control plane (persistent) | $73/mo | k3s; EKS only in `infra/envs/lab-eks`, ephemeral |
| RDS | ~$18+/mo | Decided in [ADR-0008](docs/adr/0008-production-database-on-its-own-server.md): self-managed Postgres on its own `t4g.small` EC2 server (~$14/mo) |
| GPU instances | $0.30+/hr | CPU inference; Bedrock for heavy lifting |
| **Always-on staging node** | $11/mo | On-demand staging — `make staging-up`, ~$1/mo |

### 2. Images must be `linux/arm64`

The node is Graviton (`t4g.medium`). Always `docker buildx build --platform linux/arm64`.
An amd64 image will build fine locally and fail on the cluster.

### 3. The agent never gets write access

This is the architecture, not a preference:

- `agent` — reasoning. **Read-only** credentials. Its only output is a row in `proposals`.
- `executor` — the only component that mutates anything. Scoped RBAC and IAM. Acts solely on
  proposals that passed policy and (where required) human approval.

If a change would let `agent` call the Kubernetes or AWS write APIs, that change is wrong.
Say so rather than implementing it.

### 4. Nothing reaches prod without passing staging

The promotion path is `local` → `staging` → `prod`. `lab-eks` is a portability chapter, **not** a
promotion tier.

**Build once, promote the artifact.** Images are built once and tagged by commit SHA. The digest
that passed staging is the digest deployed to prod — never a rebuild, because a rebuild is a
different artifact and staging then tested something else. `promote.yml` refuses any digest that
did not pass staging.

Environments differ **only** by a Helm values file and a pinned digest. A template change needed
for one environment and not another is a defect, not a special case.

See [ADR-0004](docs/adr/0004-environment-strategy-and-promotion.md).

### 5. Secrets never enter git

The repo goes public at v1, so **history** must be clean, not just the current tree. No real
account IDs, ARNs, endpoints, or keys in any committed file. `.env.example` carries placeholders
only. gitleaks runs pre-commit.

---

## Conventions

**Python** (services) — 3.11, FastAPI, SQLAlchemy 2.x, Pydantic v2, `ruff` + `mypy`, `pytest`.
Every LLM output is validated against a Pydantic schema before it touches the database.

**TypeScript** (mobile) — Expo SDK, strict mode, no `any`.

**Terraform** — modules in `infra/modules/`, environments in `infra/envs/`. Never `apply` without
showing the plan first. Never `apply` to `prod` without being asked.

**Helm** — one umbrella chart in `deploy/charts/kaval`. Environments differ **only** by values
files. If `lab-eks` needs a template change that `prod-k3s` doesn't, the portability claim is
broken — flag it.

**Commits and branches** — conventional commits (`feat:`, `fix:`, `docs:`, `infra:`, `chore:`),
with the Jira key where there is one; `scripts/dev/check_commits.py` enforces it in the `commit-msg` hook
and in CI. No smart-commit commands (`#done`, `#comment`): commits use the GitHub noreply email, so Jira
would ignore them; status moves with `jira-sync.py` ([ADR-0011](docs/adr/0011-commit-convention-and-jira-link.md)). Work happens on a short-lived `type/KAV-<n>-slug` branch
and merges to `main` with `--no-ff` once green. **From `KAV-24` on, it merges through a pull request, and
only after every CI check is green.** That's a written rule, because GitHub Free can't enforce it on a private repo
(decided 2026-09-27; enforcement arrives with the public repo in Phase 8). No environment branches and no per-tool
branches. See [docs/contributing.md](docs/contributing.md).

**Where things go** — the README's repository map is authoritative. Scripts are split by what
they touch: `scripts/ops/` (AWS, database), `scripts/tracking/` (Jira, Confluence, dashboard),
`scripts/dev/` (local setup). Migrations live in `migrations/`. **The top level holds only files a
tool requires there** (README, CLAUDE.md, ROADMAP, Makefile, pyproject.toml, `compose.yaml`, dotfiles);
a tool's own config lives next to that tool, e.g. `scripts/tracking/dashboard.toml`,
`docs/architecture/architecture.toml`, Alembic in `pyproject.toml`. `docs/repo-guide.md` explains
every folder and file; update it in the same change whenever a folder is added, moved or starts
being used.

---

## Definition of Done

Every unit of work, before it counts as finished:

1. Code merged, CI green
2. Lab doc in `docs/labs/` — a stranger could reproduce it from zero
3. ADR in `docs/adr/` **if a decision was made** (see ADR-0001 for the format)
4. Entry appended to `docs/journal/YYYY-MM-DD.md`
5. Cost impact noted in `docs/cost/` if spend changed
6. `docs/architecture/architecture.toml` updated **if a component was added, removed or rewired** — the diagram grows because this is a gate, not because anyone remembers
7. **Deployed to `staging` and verified before `prod`.** Never straight to prod, ever. Stories labelled
   `uat` are also **signed off** there first (`jira-sync.py uat KAV-<n> pass`,
   [ADR-0012](docs/adr/0012-user-acceptance-testing.md)).
8. **Jira reflects reality** — the relevant story/epic transitioned to its true status (Done,
   In Progress, whatever actually happened), its acceptance criteria checked off to match, without
   being asked. A Jira board that lags the repo is worse than no board.
   `python scripts/tracking/jira-sync.py tick KAV-<n>` then `transition KAV-<n> Done`.
9. **Dashboard and Confluence regenerated and republished** if anything they derive from changed —
   `make docs-sync` (runs `scripts/tracking/dashboard.py` and
   `scripts/tracking/publish-confluence.py`), then republish the artifact. Both are generated mirrors of the repo; a stale mirror
   that looks current is a worse failure than an honestly empty one, so this happens as part of
   finishing the work, not as a separate favor when asked.

10. **Documentation sweep, unasked.** When a fact changes (a price, a limit, a location, a design
   choice), grep the whole repo for every statement of the old fact (excluding dated journals,
   which are records of their day) and correct each one in the same change. Then walk every
   surface: repo docs (ADRs, labs, learn pages, runbooks, cost, roadmap, architecture, repo guide,
   README), Jira, Confluence, dashboard, vault, GitHub. The end-of-turn summary names the surfaces
   updated, so a gap is visible without anyone having to ask.

This is not "check in occasionally" — it means: after every meaningful change, before considering
the turn finished, actually run the regeneration commands and actually touch the Jira issue, the
same way `git commit` isn't optional just because no one asked for this specific commit.

`docs/runbooks/` and `docs/labs/` are dual-purpose: human documentation *and*, for runbooks, the
corpus the agent retrieves from. Writing either improves the product, not just the docs — which is
exactly why item 9 isn't optional when they change.

---

## Commands

```bash
make help          # all targets
make dev           # the Compose stack, on the AWS dev server (starts it if needed)
make dev-tunnel    # forward gateway :8000, Postgres :5432, Ollama :11435 to the laptop
make signals SCENARIO=oom-crashloop   # write one fake incident's signals (no SCENARIO: list them)
make correlate       # group signals not yet in an incident into incidents, one pass
make index-runbooks  # sync docs/runbooks/ into the pgvector retrieval index
make context INCIDENT=<uuid>  # print the context (runbooks, history, changes) built for one incident
make diagnose INCIDENT=<uuid> # ask the local model to diagnose it; writes a proposal, or nothing at all
make policy-check TYPE= BLAST_RADIUS= CONFIDENCE= [REVERSIBLE=1]  # classify one hypothetical action against policy/, no database
make evals [ONLY=name] [JSON=path]  # score the 20 golden incidents against the real pipeline
make bench         # measure the local model shortlist
make test          # unit + policy tests
make lint          # ruff + mypy, the same target CI runs
make sync          # install exactly what uv.lock pins into .venv
make lock          # after editing pyproject.toml's dependencies; commit uv.lock with it
make up            # provision the AWS node (~5 min, starts billing)
make down          # destroy the node, keep state (~$2/mo)
make cost-report   # current month-to-date spend
make devbox-up     # start the AWS dev server (ADR-0007); it stops itself after 1 h idle
make devbox-down   # stop it now
make migrate       # apply database migrations
make db-health-check     # run the database's start-up health check by hand (pg_isready + sanity query)
make db-restore-snapshot # restore the database's data volume from an EBS snapshot (SNAPSHOT=<id>, else newest)
make docs-sync     # regenerate dashboard + republish Confluence
make jira EPIC=KAV-6   # an epic's stories and status
```

---

## Working style here

- Phases are sequential and gated — check `ROADMAP.md` for where we actually are before
  suggesting work from a later phase.
- Sessions are 2–3 hours, roughly weekly. Prefer finishing one thing completely over starting three.
- When something is uncertain (a price, an API shape, a version), **verify it** rather than
  recalling it. Prices and free-tier terms in particular change.
- The `claude-api` skill is the authority on Bedrock/Anthropic model IDs and pricing. Use it
  rather than remembered figures.
- **Plain-language recap at phase/story boundaries, unprompted.** When a story or a `ROADMAP.md`
  phase finishes, and again right before starting the next one, give a short what/why/how recap
  in plain language — no unexplained jargon, written for the gist rather than the implementation.
  This is in addition to the ADR/lab/journal entries, which stay technical.

## Vault

This repo sits under `Desktop\Claude`, so the knowledge vault applies. Keep
`vault/projects/kaval.md` current and append to `vault/daily/YYYY-MM-DD.md` at wrap-up.
