# ADR-0071 — The public repo gets a security policy, named owners and a weekly Scorecard

- **Status:** Accepted (plan approved by Roshan 2026-11-01). Files and tests built; the switch for private reporting and the first Scorecard reading come after the merge ([Lab 71](../labs/lab-71-a-security-policy-owners-and-a-weekly-scorecard.md))
- **Date:** 2026-11-01
- **Deciders:** Roshan
- **Story:** `KAV-109` · Phase 8 "public-repo hygiene" line · builds on [ADR-0010](0010-ci-pipeline-and-supply-chain.md) (pinned actions, scanned history) and the repo going public on 2026-09-30 (`KAV-45`)

## Context

The repository has been public since 2026-09-30, and secret scanning, Dependabot, pinned actions and branch protection were already in place. Three things a public project is normally expected to have were missing: a way for a stranger to report a vulnerability without posting it publicly, a written owner for the folders where a mistake costs most, and a measured score of how the repository is run. GitHub's own community profile showed 57% health and no security policy.

## Decision

1. **`.github/SECURITY.md`** says how to report privately (GitHub's "Report a vulnerability" form), what to expect (acknowledged in 7 days, fix or written decision in 30), what is in scope (the code, the chart, the Terraform, the workflows, any secret in the history, any way around "the agent only writes proposals") and what is not (testing against the running systems, load, social engineering, third-party tools themselves). It names no e-mail address and no account.
2. **GitHub's private vulnerability reporting is switched on** (a repository setting, done separately and on Roshan's own go, after the merge so the setting and the file go live together).
3. **`.github/CODEOWNERS`** names Roshan as owner of everything and lists the costly folders (`.github/`, `infra/`, `deploy/`, `policy/`, `services/executor/`, `scripts/release/`, `deploy/promotion/`). **The branch rule "Require review from Code Owners" stays off**, and the file says so.
4. **`.github/workflows/scorecard.yml`** runs OpenSSF Scorecard every Sunday and by hand. It reads the repository, uploads the result to the repository's own Security tab, publishes nothing to OpenSSF's public service, and is not a required check.
5. **Tests** (`scripts/dev/test_repo_hygiene.py`) fail if the policy loses its private-report link, if CODEOWNERS names a path that does not exist or has no default owner, if the Scorecard workflow gains a permission, publishes outside the repository, or keeps credentials, and if any action in any workflow is not pinned to a full commit SHA.

## What real companies do, and why this one

1. **Policy file plus the platform's private reporting form** (chosen): the pattern GitHub itself recommends; the report arrives as a private draft advisory that can become a public one with a CVE when fixed.
2. **A security e-mail address:** common, but it publishes a personal address and splits reports across two places.
3. **A bug-bounty platform:** right for a product with customers and money to pay; not for a learning project.
4. **CODEOWNERS with mandatory owner review:** the standard on a team. On a one-person repository GitHub does not let an author approve their own pull request, so the rule would lock out the only maintainer. Chosen instead: the file as the written list and an automatic reviewer request, with the rule left for the day a second maintainer joins.
5. **Scorecard publishing its result to OpenSSF** (the badge): gives a public score, but sends data out and adds a thing to keep honest. Deferred; the result in the Security tab is enough to act on.

## Consequences

- **CODEOWNERS does not protect `main`.** What stops a bad change is the eleven required CI checks (including the promotion guard). Said in the file and here so nobody reads it as a gate.
- **One Scorecard check will read "unknown"**: Branch-Protection needs a token that can read the repository's admin settings, and the default workflow token cannot. That rule is verified by hand in the lab. A fine-grained token would fix the score but adds a secret to keep; not worth it for one line.
- **The score will not be perfect and that is allowed.** Checks that cannot pass for a one-person repository (code review by another person, for example) are recorded in the lab with the reason, not hidden.
- **Cost $0.** Public-repository Actions minutes and code scanning are free.
- **A new workflow is a new thing to keep current.** Dependabot already bumps the pinned actions weekly, and the pin test fails if one is ever left as a tag.

## Testing

`scripts/dev/test_repo_hygiene.py`, 13 tests. The negative cases: the pin check refuses a tag, a branch name and a short SHA; the workflow test fails on any extra permission, on `publish_results: true` and on a checkout that keeps credentials; CODEOWNERS fails on a path that is not in the repository.
