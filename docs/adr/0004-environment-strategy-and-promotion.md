# ADR-0004 — Environment strategy and the promotion path

- **Status:** Accepted
- **Date:** 2026-09-09
- **Deciders:** Roshan

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

**Accepted limitation.** Staging is created fresh each time, so it holds no accumulated data. It
cannot catch problems that only appear with months of production data — index bloat, table growth,
retention. Those need to be caught in production monitoring instead, and that is a real gap rather
than a solved one.

**Revisit if:** releases become frequent enough that five minutes of spin-up per release is
material friction, or if a data-shaped bug reaches production that a long-lived staging
environment would have caught. Either would justify a scheduled always-on staging node at roughly
$5–11/month.
