# ADR-0030: CI publishes images through a push-only OIDC role, and staging is pinned by pull request

**Status:** Accepted
**Date:** 2026-10-08
**Related:** [ADR-0004](0004-environment-strategy-and-promotion.md) (build once, promote the artifact),
[ADR-0010](0010-ci-pipeline-and-supply-chain.md) (CI builds but never pushes),
[ADR-0013](0013-component-versions-and-release-naming.md) (versions and release names),
[ADR-0025](0025-flux-gitops-and-ecr-bootstrap.md) (Flux pulls from Git; the one hand-pushed image),
`KAV-61`, Lab 32

## Context

`ROADMAP.md`'s `release.yml` line asks CI to build the images once, push them by digest and deploy
staging. ADR-0010 deliberately left CI unable to push, so this is the first time GitHub Actions
needs to do something in the AWS account. The only image prod runs today, `sha-ffb436b`, was built
and pushed from a laptop as a one-off (ADR-0025).

Two questions, in order:

**1. How does a GitHub job get permission to write to ECR?** The images live in ECR (the node pulls
them with its own role, ADR-0025), and ECR is inside the AWS account. Any pipeline that publishes
needs *some* door in. The question is how wide.

**2. How does the new tag reach staging?** ADR-0004 says environments differ only by a values file
and a pinned image. Prod already deploys by pull: a tag is committed under `deploy/gitops/prod/` and
Flux inside the cluster applies it. Staging is the rehearsal for prod, so it should be deployed by
exactly the same mechanism, otherwise the rehearsal proves less.

A mistake in my own first framing is recorded here because it shaped the options. I first described a
"CI never touches AWS" option. That option cannot exist: publishing to ECR is itself an AWS call. The
real choice is the *size* of the door, which is what the table below compares.

## Decision

**1. GitHub reaches AWS through OIDC, into a role that can only push images.**
`infra/modules/ci-publish` creates `kaval-ci-publish`:

- **Who may assume it:** a GitHub job from this repository running on `refs/heads/main`. The
  condition is `StringEquals` on the token's `sub`, with no wildcard, so a pull request
  (`...:pull_request`), a fork, a tag or any other branch is refused. The audience must be
  `sts.amazonaws.com`. The repository is written in GitHub's **immutable** form, with its numeric
  owner and repository ids (`repo:roshanoommenreji@37763145/kaval@1388844431:ref:refs/heads/main`),
  which this repository's tokens use. The ids cannot be inherited by someone who later registers
  the same name, so it is stricter than `owner/name`. My first version used the name-only form and
  the first release run was refused; the real subject was read from CloudTrail and the role
  corrected (Lab 32).
- **What it can do once assumed:** log in to the registry, upload layers, put images and describe
  images, on the five Kaval repositories only (`modules/ecr` ARNs). Not delete, not read secrets, not
  start servers, nothing outside ECR. ECR tags are immutable, so it cannot overwrite an existing
  image either.
- **How long:** a session lasts at most an hour. There is no stored key anywhere.
- **The role's ARN** goes in a repository *variable* (`AWS_PUBLISH_ROLE_ARN`), not a secret and not a
  file, because it contains the account id (constraint 5). It is an address, not a credential: the
  trust policy decides who can use it.

**2. The workflow is split so no job has both powers.** In `release.yml`, `publish` can reach AWS
(`id-token: write`) but has read-only access to the repository. `propose` can write a branch and a
pull request but has no `id-token`, so it cannot reach AWS. A compromise of either one gets half of
what a compromise of a combined job would.

**3. Staging is pinned by pull request, and tags in Git are what Flux pulls.** After publishing,
`propose` runs `scripts/release/pin_staging.py`, which rewrites the four image tags in
`deploy/gitops/staging/helmrelease.yaml` and `deploy/environments/staging/values.yaml` together, and
opens a pull request. Nothing pushes to `main`. The same branch protection and required checks apply
as to any change. Prod is untouched: only `promote.yml` (ADR-0032) ever writes to
`deploy/gitops/prod/`.

**4. CI on the bot's pull request, and who starts it.** Opening the pull request needs the repository
setting "Allow GitHub Actions to create and approve pull requests", enabled on 2026-10-08 (repo-wide;
with zero required reviews it adds no ability to merge that the checks do not still gate). GitHub
creates the CI run for that pull request but **holds it as `action_required`** until a person approves
it (the Actions tab, "Approve and run", or `gh api -X POST .../actions/runs/<id>/approve`). That is
kept as the deliberate human step before staging moves. I first expected the opposite (that CI would
not run at all for events made with the built-in token) and added a `workflow_dispatch` of `ci.yml` as
a workaround; the live run showed the dispatched checks did not satisfy the pull request (it stayed
`BLOCKED` with none listed until the held run was approved), so the workaround was removed, along
with the `actions: write` permission it needed.

**5. What starts a release.** A push to `main` that changes an image's inputs (`services/`,
`migrations/`, `policy/`, the lock files, the backup scripts), or a manual run. Editing the workflow
itself does not release, because it changes no image. The pin
pull request only touches `deploy/`, which is not on the list, so merging it cannot start another
release.

**6. Build once, in the pipeline that publishes.** Each image is built on a native arm64 runner,
checked (arm64, unprivileged user), scanned with Trivy for fixable HIGH/CRITICAL findings, and only
then pushed. What is scanned is exactly what is pushed. The digest is read back from ECR after the
push, so the recorded value is the registry's, not the runner's. CI's own image build on pull requests
remains a throwaway check.

**7. Tag and digest.** Images are tagged `sha-<first 7 of the commit>` (ADR-0025's scheme). The
manifests pin the tag, and the digest is recorded alongside it (job summary and pull request body).
Because tags are immutable, the two are equivalent today. Pinning `@sha256` in the chart is a
possible hardening; it needs a chart change and is left until `promote.yml` needs to compare digests.

**8. A fifth repository.** CI builds five images, one of them `backup` (the nightly dump), but
`modules/ecr` had only four repositories. `kaval/backup` is added so every image CI builds has
somewhere to go. It costs nothing until an image is stored in it.

## Alternatives considered

| Option | Why not |
|---|---|
| **Build and push from a laptop** (`make release`) | Needs no door for CI, but the artifact then depends on one machine's state and a human, and "only a merged commit produces an artifact" (ADR-0010) is lost. Already tried once, as the bootstrap push. |
| **A long-lived access key in GitHub secrets** | The old way. It never expires, and a leaked CI secret is a common route into an account. OIDC removes the key altogether. |
| **One bigger role that also wakes staging and runs the smoke test** | The right shape for a fully automatic pipeline, but it can start servers and run commands on them. Deferred until the narrow role is proven; it would be a *second* role so this one stays push-only. |
| **Commit straight to `main` from the workflow** | Skips the pull request and the required checks. ADR-0025 rejected `flux bootstrap` for the same reason. |
| **A GitHub App or personal token to open the pull request** | Would make CI start without the approval click, but adds a secret to create, store and rotate. The click is acceptable at this release rate. Revisit if it becomes a chore. |
| **Create a new GitHub OIDC provider in this module** | The account already has one (created on 2026-09-26 by the `stock-trader` project's Terraform). There is one per account, so creating it would fail, and adopting it would let this repo's `destroy` remove another project's login. It is looked up read-only instead. |
| **Pin `@sha256` in the chart now** | See Decision 7. |

## How this compares with common industry practice

| Practice | Here |
|---|---|
| OIDC federation instead of stored keys | Yes |
| Trust pinned to repo and branch, no wildcards | Yes |
| One narrow role per purpose | Yes (push-only; a second role later) |
| Pull-based GitOps, CI has no cluster credentials | Yes (Flux, already live) |
| Immutable tags, scan before publish, actions pinned to a commit SHA | Yes |
| Separate AWS accounts per environment | No. One account, separated by names and tags. Splitting is real work and the budget guards are per account. |
| Human approval before production | Not yet. The repo is public, so GitHub Environments with required reviewers are available; the ROADMAP line to switch `promote.yml` to them remains. |
| Signed images and attestations, cluster refuses unsigned | Not yet. A later hardening step. |
| A GitHub App for bot pull requests | Not yet (see the alternatives above). |

## Consequences

**Easier.** An image exists in ECR for every merged change to a service, scanned and with a recorded
digest, without a laptop. Staging's tag changes through the same review path as any other change. The
publishing job has no more reach than its job needs.

**Harder.**

- There is now an IAM trust to keep correct. The failure that matters is the `sub` condition being
  loosened; it is one line in one file, shown in the plan and checked on the live role.
- The account's GitHub OIDC provider is owned by another project. If that project's Terraform removes
  it, publishing here stops until it is restored. The data lookup fails loudly rather than silently.
- A pin pull request waits for a human to merge it. That is the point (the rehearsal for prod is
  reviewed), but a forgotten pull request means staging does not move.
- The required checks are matched by name. If a job in `ci.yml` is renamed, protection and the
  workflow drift apart and pull requests (including the bot's) block until protection is updated.

**Not done here, and why.** The smoke test, the record that a digest passed staging, component
version bumps and tags (ADR-0013) and the generated change record are the next stage of `release.yml`.
They need staging to be up, and the pass record is what `promote.yml` will check, so they are designed
together with it.

**Revisit if** releases become frequent enough that approving and merging the pin by hand is a chore
(add the second role and automate the wake and smoke test, or a GitHub App for the pull request).

## Found while writing this

- The repository has been **public since 2026-09-30** (a deliberate early step, journal that day) and
  `main` has had branch protection with required checks since then, but `CLAUDE.md`, `contributing.md`,
  the README, the budget plan and several ADRs still said "private until v1" and "a written rule, not
  enforced". All corrected in the same change. Two gaps in the protection are recorded rather than
  changed silently: `helm`, `image executor` and `image backup` were not required checks (added on
  2026-10-08, `KAV-62`; all eleven are required now, `promotion-guard` joined on 2026-10-09), and `enforce_admins` is off.
- Asked whether a public repo is safe, I checked rather than assumed: no AWS account id, key or ARN in
  any tracked file or in all history (only `000000000000` test data and AWS's published example key),
  commits use noreply addresses. It found two open settings, fixed the same day: GitHub secret scanning
  and push protection were off (now on), and outside contributors' runs needed approval only the first
  time (now every time). Prod's Flux reads this repo anonymously, so going private would also need a
  deploy key (ADR-0025).
- Because the repo is public, GitHub-hosted runners (arm64 included) are free, so the Actions-minutes
  budget in ADR-0010 and the cost plan no longer applies.
