# ADR-0004 — Environment strategy and the promotion path

- **Status:** Accepted
- **Date:** 2026-09-09
- **Deciders:** Roshan
- **Amended by:** [ADR-0008](0008-production-database-on-its-own-server.md) (2026-09-26): staging and prod each get their own database server from the same module. Dev keeps Postgres in a container on the dev server. That is a *placement* difference, not an engine one (same `pgvector/pgvector:pg16` image everywhere), so the parity rule holds. The $25 ceiling cited below is now $40.

## Context

The project had no non-production environment and no delivery pipeline. `deploy/environments/`
contained `local` (k3d on a laptop), `prod-k3s` (the AWS node) and `lab-eks` (the Phase 8
portability chapter). Only the first two are real deployment targets, and neither is a *gate* —
code would have travelled from a laptop straight to the thing being called production.
`.github/workflows/` was empty. The Jira design stopped at "nine epics and some labels".

For most personal projects that is a defensible shortcut. Here it is not, for two reasons.

**It is the gap most visible to the audience this project exists for.** Roshan is a Release
Manager. The obvious interview question — *what was your release process?* — had no good answer,
and "I pushed to main and it deployed" is a worse answer from a release manager than from a
developer.

**It contradicts the product.** Kaval's entire thesis is gated change: a proposal is validated,
policy-classified, human-approved, executed, then verified. A delivery process without those same
properties argues against the architecture it delivers.

The constraint is the $25/month ceiling, and a second environment means a second cluster. There is
no cheaper way to get real isolation — namespaces share a node, a control plane and a kernel, so
they cannot test node-level changes, k3s upgrades, or spot reclamation, which is the class of
change most likely to take production down.

Three shapes were costed:

| Option | Add | Project total | Parity |
|---|---|---|---|
| Namespaces on the prod node | $0 | ~$47 | Application only. Cannot test infrastructure change. |
| `t4g.small` always-on staging | ~$5.40/mo | ~$74 | Partial — 2 GB cannot hold Gemma 3 1B alongside k3s and Postgres |
| `t4g.medium` always-on staging | ~$11/mo | ~$102 | Exact, and idle roughly 95% of the time |
| **`t4g.medium` on demand** | **~$1/mo** | **~$52** | **Exact** |

## Decision

**Staging is a genuine second cluster — its own `t4g.medium` spot node, its own k3s, its own
etcd, its own database, its own VPC — created per release and destroyed after.**

`make staging-up` provisions it via Terraform and lets Flux reconcile, in about five minutes. It
self-destructs after four idle hours. Because it exists for hours rather than weeks, it costs
about **$1/month** and the $25 ceiling stands.

Parity with production is exact: same instance type, same 4 GB, same Gemma 3 1B. That matters
specifically here, because memory pressure on a 4 GB node is this project's binding constraint,
and a smaller staging node would fail to catch precisely the problem most likely to occur.

The promotion path is `local` → `staging` → `prod`. **`lab-eks` is not part of it** — it exists
to prove chart portability in Phase 8 and is deliberately excluded, because a portability
experiment is not a quality gate.

Two supporting rules:

**Build once, promote the artifact.** Images are built once, tagged immutably by commit SHA,
pushed to a shared ECR. The digest that passed staging is the digest deployed to production.
Nothing is ever rebuilt for prod — a rebuild is a different artifact, and then staging tested
something else. `promote.yml` refuses a digest that did not pass staging, and that refusal is
tested.

**Environments differ only by values.** A Helm values file and a pinned image digest. Anything
that requires a template change breaks the same portability claim ADR-0002 makes about EKS.

## Consequences

**Easier.** A real answer to the release-process question, backed by generated change records and
measured DORA metrics rather than assertion. Infrastructure changes get tested before production
sees them. Every release exercises the from-scratch rebuild path, which is the same property
Phase 4's exit gate tests — so the recovery path is continuously verified rather than annually
rehearsed.

**Harder.** Five minutes of spin-up before a release, and no staging cluster sitting there to poke
at midweek. A second Terraform environment to keep working. The pipeline itself is roughly two to
three sessions of work, folded into Phases 1, 3 and 4.

**Cost.** ~$1/month, project total ~$47 → ~$52. Ceiling unchanged at $25.

**~~Accepted limitation.~~ Superseded by [ADR-0005](0005-data-durability-and-staging-seeding.md).**
This originally read: *"staging is created fresh each time, so it holds no accumulated data… that
is a real gap rather than a solved one."* Roshan questioned it, and it did not survive the
question — the nightly production snapshot already existed, so seeding staging from it was a
handful of lines rather than a constraint. `make staging-up` now restores the latest sanitised
production dump, which closes most of the gap and makes the backup restore-tested on every
release.

The paragraph is left visible rather than edited away, because a decision reversed with a reason
is more useful to a reader than a decision that appears to have been right first time.

**Revisit if:** releases become frequent enough that five minutes of spin-up per release is
material friction, or if a data-shaped bug reaches production that a long-lived staging
environment would have caught. Either would justify a scheduled always-on staging node at roughly
$5–11/month.

## Amendment, 2026-10-08 — `infra/envs/staging` built and proven live (`KAV-57`, Lab 29)

The decision above stands. This records what building it settled.

- **Same modules, different `name_prefix`.** `network`, `node` and `iam` had `"kaval-prod"`
  hard-coded and now take a required `name_prefix`. Prod's rendered names are unchanged. The
  node's Kubernetes namespace, SSM parameter path and the `deploy/gitops/<env>` directory Flux
  follows all derive from it, so there is no second set of variables to keep in step.
- **Shared ECR, not duplicated.** Images live in one set of repositories, created by prod's
  Terraform. Staging computes the four repository ARNs from the caller identity and only gets
  permission to pull; it does not instantiate the ECR module (it would try to create
  repositories that already exist).
- **Own VPC (`10.61.0.0/16`), node, database server and backups bucket.** No budget module of its
  own: the account-wide guardrail's hard stop already stops every running `Project=kaval`
  instance, staging's included. No Slack: two environments sharing one Socket Mode app would
  split events between them.
- **On-Demand node by default.** Staging exists for hours, so the ~$0.012/hr premium is pennies,
  and Spot `t4g.medium` capacity was unavailable in all three AZs on 2026-10-06 and 2026-10-08
  while On-Demand launched at once. Staging is the environment that must come up when asked.
- **Destroyable on purpose.** The database module gained `termination_protection` (staging sets
  it false). The data volume keeps `prevent_destroy`; teardown releases it from state and deletes
  it by hand, a deliberate, separate step (see the ADR-0008 amendment of this date for why).
- **Measured, not estimated:** 48 resources applied in 71 seconds; node `Ready`, Flux
  reconciling from `main`, and four services running within a few minutes of that. The
  "about five minutes" in the Decision above holds.

**Not built, still separate `ROADMAP.md` lines:** `make staging-up`/`staging-down` and the
four-idle-hour self-destruct (until then staging is destroyed by hand; built the same day, see
the amendment below), seeding from a sanitised prod snapshot (`restore.sh`, `anonymise.sql`;
staging comes up with an empty database), and `release.yml`/`promote.yml`/`rollback.yml`.

## Amendment, 2026-10-08 — `make staging-up`/`staging-down` and the idle stop (`KAV-59`, ADR-0029, Lab 30)

The decision stands, with one word changed. Where this ADR says staging "self-destructs after four
idle hours", it **parks itself**: a Lambda scales the node to zero and stops the database server, and
`make staging-down` is the destroy. [ADR-0029](0029-staging-parks-itself-when-idle.md) has the
reasoning (Terraform cannot run from inside AWS without a very broad role). The cost of the change is
about $1.6/month of disk while staging is parked, against ~$0.50/month when it is destroyed straight
after each release.

`make staging-up` builds a fresh staging or resumes a parked one and waits until the pods answer;
`make staging-status` shows the database state and the pods; `make staging-down` destroys everything
and counts what is left. Still not built: seeding from a sanitised prod snapshot, and the release
workflows.

## Amendment, 2026-10-08: how the artifact is published and reaches staging (KAV-61)

"Build once, promote the artifact" now has a first working half. `release.yml` builds the five
images in CI on a merged change, scans them, and pushes them to ECR as `sha-<short>` through a
push-only OIDC role; it then opens a pull request that points **staging** at that tag, so staging is
deployed the way prod is (a tag in Git, pulled by Flux). Prod is not touched by it. The decision and
its alternatives are in [ADR-0030](0030-ci-publishes-images-by-oidc-and-staging-is-pinned-by-pull-request.md).

Because ECR tags are immutable, a tag and its digest are equivalent today; the digest is recorded
beside the tag.

**Amended 2026-10-09 (KAV-63, [ADR-0032](0032-promote-workflow-and-the-promotion-guard.md)).** `promote.yml`
now exists: it refuses a tag that is not on the passed-staging record, whose digests ECR no longer
holds, that is not ahead of prod, or whose release has a `uat` story not signed off, and otherwise opens
the pull request that pins prod. A CI check, `promotion-guard`, makes it the only way in.

**Amended 2026-10-10 (KAV-66, [ADR-0034](0034-rollback-workflow-and-what-a-rollback-may-go-back-to.md)).**
`rollback.yml` now exists: the other direction, to a version prod already ran or that passed staging, never
asking Jira, and refusing to cross a database migration unless told it was undone first. `promotion-guard`
accepts the same targets. Measured on staging: merge to healthy about 80 seconds. Rehearsing it found that a
rollback across a migration stalls on the older image's database step (see the ADR and
[Lab 35](../labs/lab-35-rollback-workflow.md)), which is the argument for expand/migrate/contract migrations
that this ADR already makes.

## Amendment, 2026-10-08: the smoke test and the passed-staging record (KAV-62)

The other half of the gate now exists. `make staging-smoke` checks a running staging read-only (pods, no
crash loop, the tag, **the running digest against ECR**, the gateway's database and migration revision,
the REST reads) and, on a pass, appends the digests to `deploy/promotion/passed-staging.json`, committed
by pull request. `promote.yml` will refuse any digest not in that file. It was proven live, including a
deliberate failure. Decision, alternatives and limits (the record is review-protected, not signed; the
model is not covered) are in [ADR-0031](0031-staging-smoke-test-and-the-passed-staging-record.md).
