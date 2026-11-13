# ADR-0094: The cluster refuses unsigned images on staging, and fails closed

**Status:** Accepted on 2026-11-13 (Roshan: `go 142`, the fifth story of the supply-chain line). Built and proven on a laptop cluster against the real signed images the same day. **Proven live on staging the same day** (his `go staging proof`; [Lab 96](../labs/lab-96-the-cluster-refuses-unsigned-images.md) Part 4): signed passes, a planted unsigned image is refused, a hard-killed checker refuses new Kaval pods and resumes by itself, a frozen rollback passes, the emergency off-switch worked once. Production is `KAV-143`.
**Deciders:** Roshan
**Related:** [ADR-0093](0093-the-cluster-checks-image-signatures-with-kyverno-watching-first.md) (the checker, built watch-only), [ADR-0092](0092-promotion-refuses-an-image-whose-signature-does-not-verify.md) (the promotion gate), [ADR-0034](0034-rollback-workflow-and-what-a-rollback-may-go-back-to.md) (rollback, which the frozen list protects), [ADR-0076](0076-a-stalled-flux-release-is-reset-not-rebuilt.md) (Flux), [threat model](../architecture/threat-model.md) T-SUPPLY-2, [Lab 96](../labs/lab-96-the-cluster-refuses-unsigned-images.md), [runbook](../runbooks/image-flagged-by-the-cluster-check.md), `KAV-142` (this story), `KAV-143` (production)

## Context

[ADR-0093](0093-the-cluster-checks-image-signatures-with-kyverno-watching-first.md) put a checker in the cluster that looks at every Kaval pod and asks whether its image carries a valid signature from `release.yml` on `main`. It only reports. It was proven live on staging on 2026-11-13: a planted unsigned image was noticed, an Event and two alerts appeared, and the checker used 44-48 MiB on the real node. That proof was the condition for this story: a checker that refuses can stop work, so it had to be shown harmless first.

Two questions were left open on purpose and are answered here:

1. **What does a dead checker mean once it refuses?** (fail open, or fail closed)
2. **What if the public signing services (Sigstore) cannot be reached?**

## Options considered (what real companies do)

| | Dead checker means | Used by |
|---|---|---|
| **Fail open** (`failurePolicy: Ignore`) | new pods start unchecked; an alert fires | teams that cannot afford any outage from the checker; the weakest guarantee: whoever can stop the checker can walk past it |
| **Fail closed, narrow scope** (`Fail`, only the app's own namespace) | new pods in that namespace wait; the cluster's own pods are never asked | the usual choice for signature checks: the guarantee holds, and the damage from a dead checker is limited to the app |
| **Fail closed, whole cluster** | the cluster can lock itself out | nobody who has been through it |

## Decision

**Refuse, and fail closed, in the Kaval namespace only, on staging.** Six parts.

1. **Two settings, flipped together:** `validationActions: [Deny]` and `failurePolicy: Fail` in `deploy/image-policy/image-validating-policy.yaml`. `Deny` with `Ignore` would refuse bad images only while the checker happens to be up. A test pins the pair and the scope.
2. **The scope is what makes `Fail` safe.** Only the Kaval namespace is in scope, so the checker's own pods (namespace `kyverno`), Flux and the cluster's system pods are never waiting on the checker. There is no way for a dead checker to deadlock its own repair.
3. **A longer answer time: 30 seconds** (`webhookConfiguration.timeoutSeconds`, the API server's maximum). The first check after the checker starts fetches Sigstore's trust data and took **8.3 seconds** on a laptop against the default 10; with `Fail`, a timeout is a refusal.
4. **Sigstore unreachable means refused, accepted.** Measured: with Sigstore's addresses pointed nowhere for the checker, a properly signed image is refused (the checker cannot fetch the trust root), and it is counted as a `fail`, so `UnsignedImageSeen` fires and the Event text carries the real reason. Embedding Sigstore's trust data in the cluster would remove the dependency, but it trades a rare outage for a time bomb: Sigstore rotates its certificates, and a stale copy would one day refuse **every** new signed release with no outage to blame. That is worse, so it is not done. Existing pods keep running; new Kaval pods wait until Sigstore is back (or the off-switch is used). It can be revisited if an outage ever bites.
5. **Measured, and the limit it leaves.** On the laptop cluster (Kyverno 1.20.0, the real policy file, the real signed images in ECR):
   - a *crashed* checker (killed hard) leaves its hook registered and **new Kaval pods are refused**; pods outside the Kaval namespace and in `kube-system` are untouched;
   - a *cleanly stopped* checker (scale to zero, a rolling update, a node shutdown) **removes its own hook**, so for the seconds until it is back (3 to 30 s measured) new pods are **not checked**. That is Kyverno's own design, to avoid locking a cluster out of its own repair; it cannot be switched off. The gap is accepted: nobody who could cause it without already being cluster-admin (who can delete the policy anyway) controls a restart, and pods that are created from the existing, already-checked deployments carry the images that passed;
   - after either, the checker resumes by itself and refuses the unsigned image again.
6. **The off-switch.** Two levels, both written in the runbook and the second one tried in the trial:
   - *Normal:* a reviewed pull request back to `validationActions: [Audit]` and `failurePolicy: Ignore`, applied by Flux.
   - *Emergency* (the checker is down and a legitimate pod must start now): delete Kyverno's two hooks (`kubectl delete validatingwebhookconfiguration kyverno-resource-validating-webhook-cfg` and `kubectl delete mutatingwebhookconfiguration kyverno-resource-mutating-webhook-cfg`; deleting only one leaves the other blocking, which the trial found). Flux does not own them. Kyverno re-registers them by itself when it is healthy again (3 to 13 s measured), so there is nothing to put back; whatever started in between was not checked and is checked by hand.

## Consequences

- **Cost:** $0 on AWS and no new memory (the checker is the same 44-51 MiB program). The live proof on staging cost about 15 cents (planned 5; staging stayed up about 3.5 hours).
- **Staging now refuses.** Every release that runs there is signed or on the frozen list (the list ends 2026-12-31; a test fails the build after that). Staging pins `sha-d1058eb` today, which is on the list (it is the newest entry), so it still builds and runs; the first signed release to be pinned there (`sha-152b59e` or later) passes on its signature.
- **A dead or unreachable checker has a visible effect now:** pods wait and the Deployment shows `FailedCreate` events. `ImageCheckerDown` (5 minutes) stays the alarm; its text says so. `UnsignedImageSeen` keeps working in refusing mode, because a refusal is counted as a `fail`.
- **Known limit found by the live proof, not fixed:** a refusal right after a checker restart can leave `UnsignedImageSeen` silent (the counter restarts at 0 and the series keeps its identity, so 1 before and 1 after look like nothing); the refusal, the Event and the pod error are unaffected, so only the alert is blind, and the next refusal fires it. A cheap fix (alerting on checker restarts too) is a candidate if it ever matters.
- **Known limit, not fixed:** if the checker is down for good (a broken Helm release, say) the monitoring pods in the same namespace cannot be recreated either, so the alarm may not get out. The staging smoke test and the runbook's checks are the backstop; production's version of this is for `KAV-143` to decide.
- **Not covered:** a pod's image changing without a pod being created or updated; pods outside the Kaval namespace; the checker's own supply chain (ADR-0093); the seconds-long gap while the checker restarts (point 5).
- **`KAV-143`:** production gets the same at its next start-up, only after it has run a signed release and the memory on its real node (which also runs the language model) is measured.
