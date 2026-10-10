# ADR-0036: ECR keeps the newest fifteen tagged images per repository, and `make up` checks that what prod pins still exists

**Status:** Accepted
**Date:** 2026-10-10
**Related:** [ADR-0024](0024-prod-landing-network-ecr-iam-node.md) (the ECR repositories; amended here),
[ADR-0004](0004-environment-strategy-and-promotion.md) (build once, promote the artifact),
[ADR-0034](0034-rollback-workflow-and-what-a-rollback-may-go-back-to.md) (rollback needs old images),
[ADR-0035](0035-computed-versions-tags-and-the-generated-change-record.md), `KAV-68`, Lab 37

## Context

ADR-0024 said every `sha-*` tagged image is kept forever, as the record of what was ever built. That was cheap
when there were three images. Now every release pushes five (about 0.45 GB) and the release pipeline makes
releases routine, so storage grows without a limit. At 30 releases it would add about $1.35 a month; at 100 it
would be the cost of a small server, to keep images nobody will ever run again.

The record of *what was promoted* does not live in ECR any more. It is in Git: `passed-staging.json`, the
change records in `docs/releases/`, and the tags `vX.Y.Z`. What ECR must hold is what can still be **run**:
the version prod pins, the versions a rollback may go back to (ADR-0034), and the newest builds.

ECR's lifecycle rules are blunt. They can expire "everything beyond the newest N" or "everything older than D
days" for a tag prefix, and nothing else. They cannot be told "except the tag prod runs". That limit shapes the
design.

## Decision

**1. A second lifecycle rule: keep the newest `keep_tagged_images` (15) images tagged `sha-*`, per repository.**
Rule 1 (untagged images expire after 7 days) stays. The count is a variable in `infra/modules/ecr`, with a
validation that refuses less than 5, because rollback and a parked prod both need older tags to still exist.

**2. Why 15.** The only way the rule can hurt is a prod that is *parked* (as it is now, on `sha-2459417`) while
more than N newer builds are pushed: its pin would then be deleted. Builds come only from changes to the
services, the migrations, the policy or the backup scripts, roughly two or three a week while the project is
active. Fifteen is five to seven weeks of that. The price of the margin is small: 15 × 0.48 GB is at most
7 GB, **$0.71 a month** at $0.10/GB, and less in practice because the images in one repository share layers.
Ten would have been $0.48 and three weeks of margin; fifteen buys more margin than it costs.

**3. A pre-flight in `make up` for the case the margin does not cover.** `promote.py preflight` reads the
four tags prod's files pin and asks ECR whether those images exist. It runs *before* the "this starts billing"
question, so a prod whose images were cleaned away never starts paying for a node that cannot pull them. On
failure it says what to do: promote a newer tag that passed staging (`promote.yml`). `promote.yml` and
`rollback.yml` already check that the images exist, so the two other ways of changing prod's tags were already
safe; this closes the third, which is starting a parked prod.

**4. The rule deletes nothing today.** Each repository holds two or three tagged images against a limit of
fifteen, so applying the policy changes no image. The first deletion happens when a sixteenth tagged image is
pushed to a repository, about a month away at the current pace.

**5. Git, not ECR, is the audit trail.** An old image being deleted does not erase the fact that it was built,
promoted or rolled back: the tag `v0.1.0`, the change record and the pass record say so. What is lost is the
ability to *run* that exact build again without rebuilding it, and a rebuild from the tagged commit is a
different artifact (ADR-0004), which is why rollback is limited to versions still present.

## Alternatives rejected

| Option | Why not |
|---|---|
| **Keep everything** (status quo) | Unbounded growth for no benefit; the audit trail is in Git |
| **Delete by age** (for example older than 90 days) | A parked prod can sit longer than any age I would pick; count tracks "how many builds have happened", which is the actual risk |
| **Keep five** | Cheapest, but two weeks of parking would be enough to lose prod's pin |
| **A second tag such as `keep-*` to protect what prod runs** | ECR has no "keep" action and no exclusion: an image with a `sha-` tag still matches the `sha-` rule whatever else it is tagged |
| **A scheduled job that deletes by hand and skips prod's pin** | A new moving part with delete rights on the registry, to save a few cents. The rule plus the pre-flight covers it with no new code in AWS |
| **Fail the build instead of expiring** | ECR does not offer it |

## Consequences

- Storage is capped, and the cap is in the budget (`docs/cost/budget-plan.md`: ECR row $0.70, total worst case
  about $40.75).
- A rollback can only reach versions still in ECR, which is at most the newest 15 builds. `rollback.yml` already
  checks existence and says so; the runbook now says it too.
- The lifecycle policy is *replaced* (Terraform destroys and recreates the policy resource, not the repository
  or its images). It is a metadata object; the plan shows five replacements and nothing else.
- ECR runs lifecycle rules about once a day and does not say when, so a deletion is not instant after the
  sixteenth push.

## Limits

- The pre-flight checks the tags in the committed files. If a person edits them by hand, the
  `promotion-guard` check is what stops that, not this.
- It checks the four deployed images, not `kaval/backup`, which prod does not run yet.
- Not run live end to end: no repository has more than 15 images, so no expiry has happened yet. What was
  proved: an ECR lifecycle **preview** of the same rule with a limit of one, on the real gateway repository,
  listed exactly the older image as the one it would expire and left the newest alone (Lab 37).

**Revisit when** releases come faster than about three a week (raise the number), or when prod is run
continuously (the parked-prod risk goes away and ten would do).
