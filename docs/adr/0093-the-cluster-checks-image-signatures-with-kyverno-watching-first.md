# ADR-0093: the cluster checks image signatures with Kyverno, watching first

**Status:** Accepted on 2026-11-13 (Roshan: "ok split, go", the fourth story of the supply-chain line). The checker is chosen, built and proven on a laptop cluster against the real signed images the same day. It is **watch-only**: it reports and refuses nothing. It was proven live on staging on 2026-11-13 (his `go staging proof`; [Lab 95](../labs/lab-95-the-cluster-checks-image-signatures.md) Part 5). Refusing on staging is `KAV-142` ([ADR-0094](0094-the-cluster-refuses-unsigned-images-on-staging-and-fails-closed.md), which answers the dead-checker and Sigstore questions left open here); production is `KAV-143`.
**Date:** 2026-11-13
**Related:** [ADR-0092](0092-promotion-refuses-an-image-whose-signature-does-not-verify.md) (the promotion gate this backs up), [ADR-0090](0090-images-are-signed-keyless-and-the-signature-lives-in-the-registry.md) (the signature this reads), [ADR-0034](0034-rollback-workflow-and-what-a-rollback-may-go-back-to.md) (rollback, which the frozen list protects), [ADR-0004](0004-environment-strategy-and-promotion.md) (environments differ only by values and pins), [ADR-0076](0076-a-stalled-flux-release-is-reset-not-rebuilt.md) (Flux), [threat model](../architecture/threat-model.md) T-SUPPLY-2, [Lab 95](../labs/lab-95-the-cluster-checks-image-signatures.md), [runbook](../runbooks/image-flagged-by-the-cluster-check.md), `KAV-140` (this story), `KAV-142` (refuse on staging), `KAV-143` (production)

## Context

After `KAV-139` the promotion gate refuses an unsigned image, but only on the path that goes through `promote.yml`. Anyone with access to the cluster, or a pin edited some other way, can still start an image nobody signed. The last line of defence is the cluster itself: it looks at each pod as it is created and says whether the image is one Kaval built.

Three facts shape this:

- **Every image running today was built before signing.** Both staging and production pin releases from 2026-11-12 or earlier. A strict checker would refuse all of them on its first day.
- **Rollback must keep working** ([ADR-0034](0034-rollback-workflow-and-what-a-rollback-may-go-back-to.md)), and the versions it can go back to are, today, all unsigned.
- **The node has 4 GB and the language model already takes a quarter of it.** Anything added has to be measured.

## Options (what real companies do)

1. **Kyverno** (CNCF, the common choice for image verification). One policy object states the rule. Heavier than the alternatives: four small programs, of which we need one.
2. **Sigstore policy-controller** (made by the people who made the signing system). One small program.
3. **OPA Gatekeeper / a hand-made admission webhook.** More to write and look after; the second is code we would own forever.
4. **Only check at the gate** ([ADR-0092](0092-promotion-refuses-an-image-whose-signature-does-not-verify.md)). Already done; does not cover a path around it.

## What was tried (laptop cluster, real signed images in ECR, 2026-11-13)

| | Result |
|---|---|
| **policy-controller 0.13.1** | **Cannot read our signatures.** `bundle support for image signatures is not yet implemented`. Our signatures (cosign 3) are Sigstore "bundle v0.3" items stored beside the image in ECR; the legacy format it does read finds nothing. |
| **Kyverno 1.20.0**, `ImageValidatingPolicy` | **Reads them.** Signed release: passes. Unsigned: flagged ("no signatures found"). Two controls with a wrong signer (`ci.yml`, a branch other than `main`): the signed release is flagged, so the pass is a real verification and not a skipped check. In refusing mode it refused the unsigned image and started the signed one; images that are not Kaval's were left alone. |
| **Memory** | One Kyverno program (admission only): **68-75 MiB** working set after a burst of requests on the laptop; **measured live on staging's real node: 44-48 MiB and 6-7m CPU, with the whole node at 61-62% of its 4 GB** (2026-11-13). The default would be 128 MiB requested, 384 MiB limit; we set 96 MiB / 256 MiB. |

## Decision

**Kyverno, the admission controller only, watch-only for now.** The decision has eight parts.

1. **One program of four.** Background, cleanup and reports controllers are switched off. A flagged image still shows as a cluster Event (`PolicyViolation`) and as a counter in Prometheus, which is what the alert reads.
2. **The rule** (`deploy/image-policy/image-validating-policy.yaml`): a pod in a Kaval namespace may start only if every Kaval image (init containers too) carries a cosign signature from `release.yml` on `main`, with the exact identity and issuer `promote.py` checks, never a pattern; or its tag is on the frozen list. A test pins the identity to `promote.py`'s own constants.
3. **The frozen list.** The 19 releases on the passed-staging record when signing began (all of them unsigned). They are listed by **tag**, which is enough because ECR tags are immutable; every tag is on the record and none is from 2026-11-13 on (both tested). Nothing unsigned is built any more, so the list can only shrink. **It ends on 2026-12-31**: a test fails the build after that date, so it cannot linger. Extending needs a new line here with a reason. Rollback may go to any listed tag until then.
4. **Check first, list second.** The signature check runs on every image and the list is the exemption afterwards. With the list first, a listed image was never checked and Kyverno reported "no policy performed a signature check on it". A test pins the order.
5. **Scope.** Only the Kaval namespace (filled in by Flux per environment), only pods. Kyverno's default is every namespace, which would let a down checker block the cluster's own system pods.
6. **Watch-only is two settings that move together:** `validationActions: [Audit]` and `failurePolicy: Ignore`. While it only watches, a checker that is down must never stop a Kaval pod from starting. `KAV-142` flipped both and decided what a down checker means ([ADR-0094](0094-the-cluster-refuses-unsigned-images-on-staging-and-fails-closed.md): fail closed, Kaval namespace only). A test pins the pair.
7. **The registry login** is the `ecr-cred` secret copied into the `kyverno` namespace by the node's start-up script (only when the Terraform switch `image_checker_enabled` is on: staging now, production at `KAV-143`). The pods' IMDS access is closed (hop limit 1), so the node's role cannot be used from a pod; the existing ECR read permission on the node role is what the login rides on. No new AWS permission.
8. **Pinned.** The chart version is exact and each of the six image settings it runs is pinned to a multi-architecture digest (all four images have `linux/arm64`). A test fails if one drifts to a floating tag. The registry address, which holds the account number, is never written in git: Flux fills it in from the `kaval-registry` ConfigMap, as it does for the chart.

## Consequences

- **Staging gets the checker at its next build** (the node script and the Flux objects are in `main`). It will report nothing on the current release, because every running release is on the frozen list: the live proof therefore planted one deliberately (Lab 95 Part 5, done 2026-11-13: noticed, not blocked, alert fired).
- **Cost:** $0 on AWS. About 70 MiB on the node. The images come from the public internet once per build (staging is rebuilt each time). No new permission.
- **The checker's own images come from two public registries** (`reg.kyverno.io`, `ghcr.io`) and are pinned by digest but not signature-checked by us. It is the same position as Prometheus (`quay.io`). Verifying Kyverno's own signature is a candidate for a later story.
- **Sigstore is called at admission time** (the Fulcio/Rekor public services). While watch-only, an outage shows as an error and nothing else. For `KAV-142` it is an open question with a real cost: a refusing checker that cannot reach Sigstore either blocks pods or stops checking. It is the first thing that story decides, together with Kyverno's cache and the option of embedding Sigstore's trust material (`trustedRoot`).
- **The boot script is nearly full.** The node's start-up script is 15,924 of 16,384 permitted bytes once packed (staging, everything on). This change spent 9 lines of it. The next addition has to move something out (for example to the S3 bucket the node already reads); `test_cost_pull.py` says so.
- **The 15-minute re-alert.** `UnsignedImageSeen` also fires for a failure that was already counted when Prometheus restarted (its 2-hour memory starts empty). It stays visible instead of being silently forgotten; the cost is one repeat per Prometheus restart.
- **Not covered:** a pod's image changing without a pod being created or updated (nothing does that); pods outside the Kaval namespace; the checker's own supply chain (above).
