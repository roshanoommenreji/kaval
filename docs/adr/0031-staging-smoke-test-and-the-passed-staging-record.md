# ADR-0031: A read-only smoke test proves a digest on staging, and a committed record says it passed

**Status:** Accepted
**Date:** 2026-10-08
**Related:** [ADR-0004](0004-environment-strategy-and-promotion.md) (nothing reaches prod without
passing staging), [ADR-0029](0029-staging-parks-itself-when-idle.md) (staging up and down),
[ADR-0030](0030-ci-publishes-images-by-oidc-and-staging-is-pinned-by-pull-request.md) (stage one:
publish and pin), `KAV-62`, Lab 33

## Context

ADR-0004 says `promote.yml` refuses any digest that did not pass staging. Stage one of `release.yml`
(ADR-0030) publishes five images and pins staging to the new tag, but nothing yet *proves* the tag
runs, and nothing *records* that it did. Without a record there is nothing for `promote.yml` to check,
and "passed staging" is a feeling rather than a fact.

Three questions:

**1. Who runs the test, and from where?** CI has a push-only ECR role and no way to reach a cluster
(deliberately: ADR-0030). Staging is also off most of the time, so a test cannot be a step that runs on
every merge.

**2. What counts as a pass?** The tempting answer is "the pods are Running". That is weaker than it
sounds: a tag can be re-pointed, a pod can be running an older image than Git says, and the gateway
can be up while it cannot reach the database.

**3. Where does the record live, and who can write it?**

## Decision

**1. The smoke test is a script a person runs against a staging that is up** (`make staging-smoke`,
`scripts/release/staging_smoke.py`). It reaches the node over Session Manager, the same way
`staging.sh status` does, and changes nothing on it. CI stays out of staging: giving it a role that can
reach the cluster is the second, wider role ADR-0030 deferred, and a release rate of a few a week does
not justify it yet. The cost of that choice is one command and about five minutes of staging per release.

**2. A pass is five things, all of which must hold.**

| Check | Why it is there |
|---|---|
| Every pod Running and ready (a finished migration job is fine) | Basic liveness |
| No crash loop: at most 2 restarts per container, each one written into the record | A service that keeps dying is not a pass. Two are tolerated, see below |
| Every service runs the tag Git says staging should run | Flux really applied the pin |
| **The digest the node pulled equals the digest ECR holds for that tag** | The bytes that ran are the bytes that were published, not just a tag name |
| The gateway reaches the database and the migration revision equals the head of the tag's own commit; `/v1/incidents` and `/v1/signals` answer 200 | The app works against real Postgres |

**Why two restarts are tolerated.** A fresh staging has an empty database. The migration job creates the
schema first, and only then does the node set each service's role password (KAV-56). The agent connects
in between, is refused (`password authentication failed for user "kaval_agent"`) and restarts once. I
first wrote the check as "zero restarts", and the first live run failed on exactly this. Zero would fail
every fresh staging for a designed sequence, so the limit is 2 and the restart is recorded in the
entry, in plain words, rather than hidden. A crash loop keeps counting past 2. This is a judgment call;
the alternative of making the agent's init container wait for a real login (not just `pg_isready`)
would remove the restart and is a chart change for another day.

The migration head is read from the commit the tag names (`git show <commit>:migrations/versions/...`),
not from whatever is checked out, so a stale clone cannot make a wrong database look right.

**3. The record is `deploy/promotion/passed-staging.json`, committed through a pull request.** On a
pass the script appends the tag, the commit, the time, the four digests that ran, the checks and their
details. `promote.yml` will read this file from `main` and refuse a digest that is not in it. A re-run
for the same tag replaces its entry rather than adding a second.

It lives in Git, not in a database or a GitHub status, because Git is already how this project records
every deploy fact (prod and staging tags are in Git; Flux reads them). It is reviewed by the same
required checks as any other change, it is history that cannot be quietly edited, and a rollback or an
audit can see exactly what passed and when.

**4. The record says what it does not prove.** Every entry carries a `not_covered` list: Ollama and the
model (no environment deploys one yet, so `/healthz`'s `ollama` check is *expected* to fail and is not
gated on), agent diagnosis and proposals (they need the model), the backup image, and Slack approval.
The point is that "passed staging" must not be read as "the whole product works".

**5. The backup image is recorded, not exercised.** (If a tag predates the `kaval/backup` repository, as `sha-ffb436b` does, the note is printed and the pass is judged on the four deployed images.) `release.yml` publishes it and the record lists its
digest under `published_not_exercised`, but staging does not deploy it (it needs prod's dump bucket).
Prod does not run it yet either (the nightly backup job is off until the dump bucket credentials exist), so
`promote.yml` pins only the four deployed images (ADR-0032 corrects what this ADR first said here).

## Alternatives considered

| Option | Why not |
|---|---|
| **CI wakes staging, runs the test, writes the record** | The right end state, but needs a second, wider AWS role (start servers, run commands). ADR-0030 kept the publish role push-only; this waits until the manual loop has shown which checks matter. |
| **Record the pass as a GitHub Environment deployment or commit status** | Lives on GitHub's servers, not in the repository, and is awkward to read from a workflow for an arbitrary older digest. |
| **A tag or git note on the commit** | Hard to review, and tags are not covered by the pull-request checks. |
| **A Jira field or a database row** | A second source of truth to keep in step; Git already is the one. |
| **"Pods Running" only** | Cannot tell a re-pointed tag from the published one, or an app that cannot reach its database. |
| **Gate on `/healthz` returning 200** | It would never pass: the Ollama check fails in every environment until a model is deployed. The test reads the database check out of `/healthz` and ignores the model check, saying so in the record. |

## Verified live (2026-10-08)

`sha-2459417` on a freshly built staging: all checks passed and the record was written. The same staging,
told to expect `sha-ffb436b` (what prod runs), **failed on three counts**: the tag, the digests, and the
migration revision (database at `a1c4f9b0e3d2`, that commit expects `8f3b1c6a2d94`). The first live runs
also failed twice on my own mistakes (a Service lookup by a label the Service does not carry, then a
stray newline in a quoted expression), which is why the remote script now has a test for that. Lab 33.

## Consequences

**Easier.** There is now a thing `promote.yml` can check, and a yes/no on whether a published digest
runs. The digest comparison closes the gap ADR-0030 noted (tags pinned, digests only recorded): the
record is in terms of digests.

**Harder.**

- The record is a file anyone who can merge to `main` could edit by hand. The protection is review and
  the required checks, not cryptography. Signed attestations (the registry signing what passed, the
  cluster verifying it) is the stronger form and is a later hardening.
- Someone has to bring staging up and run the test. A forgotten step means `promote.yml` will correctly
  refuse the digest, which is the safe failure.
- The migration-head check depends on the tag's commit being present in the clone that runs the test.
  If it is not, the script says "unknown" and skips only that comparison.

**Revisit when** a model is deployed in staging (add the diagnosis path to the test and remove the
`not_covered` line), or when releases are frequent enough that running it by hand is a chore (the second
role).
