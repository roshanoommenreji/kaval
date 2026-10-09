# ADR-0032: `promote.yml` asks four questions and opens a pull request; a CI guard makes it the only way in

**Status:** Accepted
**Date:** 2026-10-09
**Related:** [ADR-0004](0004-environment-strategy-and-promotion.md) (nothing reaches prod without passing
staging), [ADR-0012](0012-user-acceptance-testing.md) (UAT sign-off), [ADR-0030](0030-ci-publishes-images-by-oidc-and-staging-is-pinned-by-pull-request.md)
(publish and pin), [ADR-0031](0031-staging-smoke-test-and-the-passed-staging-record.md) (the record),
`KAV-63`, Lab 34

## Context

ADR-0004 promises that `promote.yml` refuses any digest that did not pass staging, and ADR-0012 that it
refuses a release with an unaccepted `uat` story. Until now both were sentences. Anyone who could merge a
pull request could edit prod's image tags by hand to anything, and nothing in CI would object. KAV-62
produced the thing to check (`deploy/promotion/passed-staging.json`). This story is the check.

## Decision

**1. A person dispatches `promote.yml` with a tag; it opens a pull request; merging it is the go/no-go.**
The workflow never touches the cluster (it has no credentials to). Prod is a Flux cluster that pulls what
is on `main`, so the pull request is the whole release action, reviewed by the same required checks as any
change. This is the same shape as staging's pin (ADR-0030), so there is one way images move, not two.

**2. The `gate` job asks four questions, and any "no" stops the workflow** (`scripts/release/promote.py verify`):

| Question | Why |
|---|---|
| Is the tag on the passed-staging record, with a digest for each of the four services? | The core rule of ADR-0004 |
| Do the digests ECR holds *now* equal the recorded ones? | ECR tags are immutable only while the image exists. A tag deleted and pushed again would match by name and be different bytes |
| Is the tag's commit ahead of prod's? | Same tag: nothing to do. Older: that is a rollback, a different act with its own workflow |
| Is every `uat` story in the release in *Ready for Prod* or *Done* in Jira? | ADR-0012 |

If the gate cannot decide (Jira unreachable, ECR unreadable, a commit missing from the clone) it **refuses**
and says so. A gate that guesses yes when it cannot see is not a gate.

**3. Which stories are "in the release".** Every `KAV-n` in the subject of a commit between prod's current
commit and the tag's commit (`git log --format=%s prod..tag`). `check_commits.py` already forces story
branches' commits to carry the key, and merge commits carry the branch name. A key that does not exist in
Jira is noted and ignored; a story without the `uat` label is never blocked by its status.

**4. Two jobs, different powers; no job has both.**

- `gate` can reach AWS (read-only: it describes four images) and Jira (read-only), and cannot write to the repository.
- `propose` can write a branch and open a pull request and has neither AWS nor Jira. It re-checks the record
  itself (`promote.py pin` does) rather than trusting the job before it.
- It runs only from `main`. AWS's trust policy (`infra/modules/ci-publish`) already accepts only this
  repository on `main`, so a branch cannot borrow the role. The same role release.yml publishes with is reused:
  this job only describes images, and a second role is more to keep correct for no extra safety.
- The workflow input is user input, so it travels through an environment variable and is validated as
  `sha-` plus hex before use.

**5. Jira is read by repository secrets.** `JIRA_SITE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, held in GitHub
Actions secrets (never in git, never shown to the assistant), used only by the `gate` job. The token is a
scoped, read-only Atlassian token created for this purpose (not the full-access one in the laptop's `.env`),
so a leak or a bad workflow change can read stories and cannot change them. A scoped token only works through Atlassian's gateway address
(`https://api.atlassian.com/ex/jira/<cloud id>`), which is what `JIRA_SITE_URL` holds.

**6. `promotion-guard` is what makes `promote.yml` the only way in.** A CI job on every pull request: if the
image tags in `deploy/gitops/prod/helmrelease.yaml` or `deploy/environments/prod/values.yaml` changed, the new
tag must be one tag on all eight lines and must be on the passed-staging record **as it stands on the branch
the change merges into**. Reading the record from the base, not from the pull request, means a change cannot
add its own proof: the record entry has to be merged first. It is a required check on `main` (the eleventh).
It compares tags only; it makes no AWS or Jira call, so it needs no secret.

**7. Only the four deployed images are promoted.** `release.yml` also publishes a backup image, but prod does
not run it (the nightly backup job is switched off until the dump bucket credentials exist), so it has no pin
in prod. This corrects ADR-0031 and the roadmap, which said prod runs it. When backup is enabled in prod it
needs its own decision on how an image that staging cannot exercise is accepted.

## Alternatives considered

| Option | Why not |
|---|---|
| **A GitHub Environment with a required reviewer** | Another click that approves the same thing the pull request merge already approves. With one person it is ceremony. Worth adding if a second maintainer appears |
| **`promote.yml` commits straight to `main`** | Skips the pull request and the required checks. ADR-0025 and ADR-0030 rejected the same shortcut |
| **Check UAT from the laptop only** | A courtesy, not a gate (ADR-0012 says the workflow refuses) |
| **Record UAT sign-offs in the repository instead of Jira** | A second source of truth to keep in step with Jira, and a pull request per sign-off. Jira already holds it |
| **A full-access Jira token** | Works with the normal site address, but a leaked or misused token could change Jira. Read-only costs one extra setting |
| **A GitHub Environment `prod` for the secrets** | Would change the OIDC subject AWS checks and break the trust policy; repository secrets, readable only by workflows on `main`, are enough here |
| **Guard on `main` after merge instead of on the pull request** | Too late: the change is already in. The pull request is the place to stop it |

## Verified live (2026-10-09)

- `promote.yml` with `sha-2459417`: all four questions answered yes (record, ECR "all four match", ahead of
  `sha-ffb436b`, 9 stories and none `uat`) and PR #80 opened. It was left **unmerged**.
- With `sha-ffb436b` (what prod runs; never on the record): refused at the first question; `propose` did not run.
- The `uat` question against real Jira: `KAV-44` (`uat`, *In Staging*) refused; `KAV-40` (`uat`, *Done*) accepted.
- `promotion-guard`: unchanged tags pass; a hand edit to `sha-deadbee` fails; `promote.py pin` output passes; and
  it passed on PR #80, a real promotion.
- First run on a bot's pull request waits for a person to approve CI, as for staging's pin. Approved by hand.
- While proving this, two new CVEs in the bundled OPA binary turned the image checks red on every pull request.
  Unrelated to this change; handled in [ADR-0033](0033-dated-trivy-exception-for-the-bundled-opa-binary.md).

## Consequences

**Easier.** There is one way into prod and CI enforces it. The pull request carries the evidence (record,
digests, what the gate checked), so the go/no-go is made with the facts in front of the person.

**Harder.**

- Merging PR #80 means the next time prod comes up it pulls the new images and runs the database migration
  against the production database. The pull request says so; nothing is applied by merging alone.
- A rollback to a tag that never passed staging (for example `sha-ffb436b`) is now blocked by the guard.
  `rollback.yml` (next) has to define how a known-good earlier version is allowed back in.
- The story link is a commit-message convention. A change whose commit subjects carry no key is invisible to
  the UAT question.
- A person with admin rights can still bypass branch protection (`enforce_admins` is off), and the record is
  protected by review rather than by signature. Signed attestations remain the stronger form.
- The repository now holds three Actions secrets. They are readable only by workflows on `main`, and a
  malicious workflow change merged to `main` could read them: the read-only scope is what limits that.

**Revisit when** a second maintainer joins (add an Environment reviewer), `rollback.yml` is built, or the
backup job is enabled in prod.
