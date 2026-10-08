# Lab 33 — `release.yml` stage two: smoke-test the published digest on staging, and record the pass

**Phase:** 4 · **Story:** `KAV-62` · **Cost:** staging for under an hour, about $0.045/hour, so a few
cents; destroyed afterwards. Nothing else (the script only reads).

[Lab 32](lab-32-release-publish-stage.md) ended with five images in ECR and a merged pull request
pointing staging at `sha-2459417`, but staging had never actually run it. This lab runs it, checks it,
and writes down that it passed, so that `promote.yml` (not built yet) has something to check. The
decisions and alternatives are in
[ADR-0031](../adr/0031-staging-smoke-test-and-the-passed-staging-record.md).

**Result:** `make staging-smoke` against a live staging running `sha-2459417` passes seven checks and
writes `deploy/promotion/passed-staging.json`. Told to expect `sha-ffb436b` instead, it fails three.

---

## 1. Bring staging up

```bash
make staging-up        # asks first; builds the node, database server and VPC, waits for the pods
```

The four service pods were `Running 1/1` about a minute after Flux applied the manifests. One of them,
the agent, had already restarted once. Section 4 explains why that is expected.

## 2. What the smoke test checks (`scripts/release/staging_smoke.py`)

It reads, and changes nothing. From the laptop it asks ECR for the digest of each image at the tag,
then runs one shell snippet on the node over Session Manager (the same route as `make staging-status`,
no SSH, no open port) and judges what came back:

| Check | What it looked like |
|---|---|
| Pods running and ready | `4 running` (the finished migration job pod is allowed) |
| No crash loop | `agent ... restarted 1x (within the first-boot tolerance of 2)` |
| Every service runs the expected tag | `ok` |
| **Running digest equals the ECR digest** | `all four match` |
| Gateway reaches the database, migrated | `migrated to a1c4f9b0e3d2`, which is the head of the tag's own commit |
| `GET /v1/incidents?limit=1`, `GET /v1/signals?limit=1` | `HTTP 200` |

The digest check is the one that matters: the node reports the digest it actually pulled
(`status.containerStatuses[].imageID`), and it is compared with what ECR holds for the tag. A tag that
was re-pointed, or a pod running something older than Git says, shows up here and nowhere else.

`/healthz` itself is **not** used as the pass signal. It returns 503 in every environment because its
Ollama check fails until a model is deployed. The test reads the database half out of its JSON and
says in the record that the model half was not tested.

## 3. Run it, and run it wrong

```bash
make staging-smoke                                   # judges the tag in staging's HelmRelease
make staging-smoke TAG=sha-ffb436b NO_RECORD=1       # a deliberate fail: the tag prod runs
```

The second run, against the same staging:

```
FAIL  every service runs sha-ffb436b: gateway runs ['sha-2459417']; ...
FAIL  running digest equals the ECR digest: gateway: node has sha256:e0daaa15..., ECR has sha256:0deb6de7...; ...
FAIL  gateway reaches the database, migrated: database is at a1c4f9b0e3d2, the tag's commit expects 8f3b1c6a2d94
```

A gate that has never said no has not been shown to work. The three failures are independent: the tag,
the bytes and the database revision each catch it alone.

## 4. What went wrong, in order

1. **The agent restarted, and the test said so.** My first version required zero restarts and failed.
   The agent log explained it: `password authentication failed for user "kaval_agent"`. On a fresh
   database the migration job creates the schema, and only afterwards does the node set each role's
   password (KAV-56). The agent connected in between and Kubernetes restarted it. The sequence is by
   design and the service recovers in seconds, so the check became "no crash loop": up to two restarts
   are tolerated and **written into the record**; a loop keeps counting past that.
2. **The gateway could not be found (my bug).** The lookup selected the Service by a
   `component=gateway` label. The Service has that label in its *selector*, not on itself, so the
   lookup was empty and every HTTP check read `000`. Fixed by matching the name.
3. **A stray newline (my bug, again).** The fix was first written with a real line break inside a quoted
   expression, which silently split the shell line. The script now has a test that every line of the
   remote snippet has balanced quotes.
4. **Old tags have no backup image.** The negative run aborted because `sha-ffb436b` predates the
   `kaval/backup` repository. The backup digest is now optional in the test (it is recorded, not
   exercised), with a printed note.

## 5. The record

On a pass the script appends to `deploy/promotion/passed-staging.json`: tag, commit, time (UTC), the
four digests that ran, `published_not_exercised` (the backup digest), the checks with their details,
and `not_covered`: Ollama and the model, agent diagnosis, the backup image, Slack approval. It is
committed in a pull request like any other change.

## 6. Tear it down

```bash
make staging-down
```

## What this does not do

- It does not prove the agent can diagnose anything: there is no model deployed anywhere yet.
- It does not stop someone with merge rights from editing the record by hand. Review and the required
  checks are the protection; signed attestations would be the stronger form (ADR-0031).
- CI does not run it. That needs a second, wider AWS role and is deferred (ADR-0030, ADR-0031).

## Reproduce

1. `make staging-up`, then `make staging-smoke`.
2. Confirm all seven checks pass and `deploy/promotion/passed-staging.json` has the tag and digests.
3. `make staging-smoke TAG=<another published tag> NO_RECORD=1` and confirm it fails.
4. `make staging-down`; the last line should count 0 instances, volumes and VPCs tagged `Env=staging`.
