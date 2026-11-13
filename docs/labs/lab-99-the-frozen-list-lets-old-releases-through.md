# Lab 99 — The frozen list really lets old releases through

**Phase:** 8 (hardening) · **Story:** `KAV-146` · **Decision:** [ADR-0096](../adr/0096-the-frozen-list-is-not-matched-so-it-is-never-checked.md) · **Cost:** $0 for Parts 1-4 (Docker on a laptop); about 15 cents planned for Part 5 (one staging session); Part 6 rides on production's next start-up.

## What you will build

The cluster refuses any Kaval image that is not signed ([Lab 96](lab-96-the-cluster-refuses-unsigned-images.md), [Lab 97](lab-97-production-refuses-unsigned-images.md)), except 19 old releases built before signing, on a "frozen list". At production's start-up on 2026-11-13 the new `frozen` step of the proof script asked a throwaway pod from one of them to start, and it was **refused**.

This lab reproduces that on a laptop with Kyverno's own tool, shows why the list inside the rule never worked, why two earlier "passes" were not passes, moves the list to where it does work, and leaves a test that asks Kyverno itself.

You need the repository, Docker and the internet (the tool's image and one public image's signature are fetched). Parts 1-4 create nothing in AWS. Parts 5 and 6 need a typed go from the owner.

## Part 1 — The symptom

```bash
AWS_PROFILE=kaval python scripts/ops/checker_proof.py prod frozen --yes
```

Expected, and what a healthy checker prints: `frozen pod: 1/1 Running`. What production printed:

```
admission webhook "ivpol.validate.kyverno.svc-fail-finegrained-kaval-images-signed" denied the request:
Policy kaval-images-signed failed: image <registry>/kaval/gateway:sha-791c44d@sha256:73033a8e... failed signature or attestation verification
```

The checker's log (`kubectl -n kyverno logs deploy/kyverno-admission-controller`) shows the check running (`verifying cosign image signature`) and failing (`no signatures found`), then the refusal. Two things to notice: the image carries a **digest** (`@sha256:...`) that the pod never had (Kyverno adds it), and the sentence is Kyverno's own, not the policy's (the policy's message begins "a Kaval image has no valid signature...").

## Part 2 — Reproduce it for free

Kyverno's command-line tool is the same engine. Make a pod from a public image Kaval did not sign, and put its own tag on a one-tag list in a copy of the real policy:

```bash
docker run --rm -v "$PWD:/w" ghcr.io/kyverno/kyverno-cli:v1.20.0 apply /w/policy.yaml --resource /w/pod.yaml
```

With the old policy (the list inside the rule: `verify > 0 || tag is listed`):

| Pod | Result |
|---|---|
| Listed tag, with the digest Kyverno adds | **refused** — "failed signature or attestation verification" (the live message, word for word) |
| Not-listed tag | refused — with the policy's own message |

The first row is the bug. The two sentences differ because they come from two different places: the rule (which let the listed image through) and a separate check Kyverno makes afterwards, *did a signature check on this image fail?* For a listed unsigned image the answer is yes, whatever the rule says.

Also tried and rejected: calling the check only for unlisted images. Kyverno then complains "no policy performed a signature check on it", which is why the old rule called the check first.

## Part 3 — Why it looked as if it worked

- **The laptop trial** (`scripts/dev/checker_trial.sh`) takes the last tag on the list as its "frozen old release". That is `sha-d1058eb`.
- **The live staging session** (Lab 96, step 5, and "a real gateway pod, deleted, was recreated") also used `sha-d1058eb`.

`sha-d1058eb`'s **gateway** and **backup** images are byte-for-byte the signed `sha-152b59e`'s (checked in ECR on 2026-11-13: the digests are identical; the executor, agent and collector differ). So the pods passed on their *signature*, and the list was never asked. [ADR-0095](../adr/0095-production-refuses-unsigned-images-from-its-next-start-up.md) had already met the same trap for the planted unsigned name and made the script refuse a base whose digest equals the signed one. Nobody applied the same question to the frozen test, so the first real use of the list was production's.

**The rule to keep:** an exception is only proved by the one thing the rule would refuse without it. Before trusting a "frozen pass", compare the image digest with the signed release's.

## Part 4 — The fix, and a test that asks Kyverno

The list moves into `matchImageReferences`, the rule that decides which images the policy looks at. Kyverno skips an image no rule matches ("even when passed to a verification function"), so a listed release is never checked and nothing fails.

```yaml
matchImageReferences:
  - expression: >-
      ref.startsWith('${registry}/kaval/') &&
      ![ 'sha-2459417', ... 'sha-d1058eb', ].exists(t, ref.endsWith(':' + t) || ref.contains(':' + t + '@'))
```

Run it for real (the test rewrites only the registry and the list, then asks Kyverno):

```bash
KAVAL_KYVERNO_CLI=1 python -m pytest scripts/dev/test_image_policy_kyverno.py -v
```

| Pod | Expected |
|---|---|
| Listed tag, with digest | let in |
| Listed tag, written as the pod is (no digest) | let in |
| Listed image beside an outside image (init container `busybox`) | let in |
| Not-listed unsigned image | **refused** |
| Listed image beside a not-listed unsigned image | **refused** |
| A bare digest, no tag | **refused** |

Then the unit tests, which pin the shape and go red against the old policy: `python -m pytest scripts/dev/test_image_policy.py`.

## Part 5 — Staging (his typed go)

`go staging smoke`, then with `scripts/ops/checker_proof.py staging <step>`:

1. `state`: policy `Deny`/`Fail`/30 s, checker ready.
2. `frozen --yes`: **`frozen pod: 1/1 Running`** (`sha-791c44d` is unsigned and different from the signed image; the script refuses a base that is not). This is the line that failed on production.
3. `signed --yes`: a signed release is still accepted (the list did not weaken the signature path).
4. `plant` then `unsigned --yes`: an unsigned image **not** on the list is still refused; `UnsignedImageSeen` rings.
5. Delete one real Kaval pod that runs an **unsigned listed** image (staging pins `sha-ba87981`; `kubectl delete pod -n kaval-staging <executor or agent or collector pod>`) and watch its Deployment bring it back (`1/1 Running`, no `FailedCreate`). This is the case that was broken in production.
6. `clean --yes`, `unplant`, `make staging-down`.

*Results: pending.*

## Part 6 — Production (at its next start-up)

The same `state`, `alerts`, `frozen --yes`, `signed --yes`, and the real-pod deletion in step 5 on a pod of the pinned release. Then KAV-146 is Done and KAV-143's and KAV-144's last lines close.

*Results: pending.*

## What to remember

- Kyverno's refusal for "a check ran and failed" is separate from the rule. Do not try to override it from inside the rule; keep the image out of what the policy matches.
- A policy file passing its tests is not the same as Kyverno doing what the file says. The new test asks Kyverno.
- Until Part 6, production runs the old policy. Its pinned release has three unsigned images (executor, agent, collector), so while it is up a *recreated* pod of one of them would be refused (the runbook's emergency off-switch is the way out).
