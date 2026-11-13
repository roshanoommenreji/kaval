# An image was flagged by the cluster's signature check

The cluster has a checker (Kyverno, `KAV-140`, [ADR-0093](../adr/0093-the-cluster-checks-image-signatures-with-kyverno-watching-first.md)) that looks at every Kaval pod as it is created and asks whether the image was signed by this repository's release workflow on `main`. While it only watches, a flagged image still starts: the alert and the Event are the whole effect. This is what to do when it speaks up, or when it goes quiet.

For a person, no automatic fix is proposed. The checker reports; it does not change anything, and the agent must not either.

## Signals

- Alert **`UnsignedImageSeen`**: a Kaval image started (or, once the checker refuses under `KAV-142`, was refused) without a valid signature and is not on the frozen list
- Alert **`ImageCheckerDown`**: Prometheus cannot reach the checker for five minutes. While it only watches, that means we are blind to unsigned images; once it refuses, it can also mean new pods cannot start
- A cluster Event `PolicyViolation` naming `kaval-images-signed`: `kubectl get events -n kaval-staging --field-selector reason=PolicyViolation`
- (Once refusing) a pod stuck without being created, with an error mentioning `kaval-images-signed`

## Likely causes

1. **A person started an image by hand**, from a tag nobody released (for example a debugging copy). Expected only in a trial; in real use it is the case the checker exists for
2. **A release that was built before signing existed (before 2026-11-13) and is not on the frozen list**, for example a rollback to an older version that was not on the passed-staging record when the list was written. The list is the 19 releases on the record then
3. **The frozen list's end date (2026-12-31) has passed** and the list was removed, but production or staging still runs an unsigned release. Release again; do not extend the list without a new ADR line
4. **A release run stopped before it signed** (Sigstore or ECR was unreachable): the image exists, has no signature, and the release run shows a failed **sign the image** step
5. **The checker cannot read the signature**, so every Kaval image looks unsigned: the `ecr-cred` secret in the `kyverno` namespace is missing or expired (the node rewrites it every 6 hours), or Sigstore's public services are unreachable from the node
6. **The checker is down** (`ImageCheckerDown`): its pod is not running (out of memory, a failed Helm release, the Flux object for it stuck)

## Diagnosis

1. Which image and which pod: `kubectl get events -n <kaval namespace> --field-selector reason=PolicyViolation` names the pod and prints the image with its digest and the reason ("no signatures found", "failed to verify certificate identity", "not verified").
2. **Is it signed?** Use the read-only command from the promotion gate, on the tag: `python scripts/release/promote.py signatures --tag <tag>` (needs your AWS login, see [verify an image](verify-an-image.md)). Three PASS lines: signed; the checker is wrong or cannot read it (cause 5). Three FAIL lines: unsigned (causes 1-4).
3. **Is the tag on the frozen list?** `grep "'<tag>'" deploy/image-policy/image-validating-policy.yaml`. On the list: it must not be flagged, so suspect cause 5 instead.
4. **Where did it come from?** `kubectl get pod <name> -o yaml` shows who created it (`ownerReferences`: a ReplicaSet from Flux's release, a Job, or nothing at all = by hand). `flux get helmrelease -A` shows what Flux thinks it deployed.
5. **The checker's own health:**
   - `kubectl -n kyverno get pods` (one admission pod, `Running`)
   - `kubectl get imagevalidatingpolicies` (`READY true`)
   - `kubectl -n kyverno logs deploy/kyverno-admission-controller --tail 50` (errors about credentials or about reaching Sigstore)
   - `kubectl -n kyverno get secret ecr-cred` (it exists, and its age is under 6 hours)
   - `kubectl top pod -n kyverno` (about 70 MiB; far above the 256 MiB ceiling means it is restarting)
   - `flux get kustomization kaval-staging-image-policy -n flux-system` (Ready)

## Remediation

- **An image nobody released:** delete the pod, find who started it and why. If it was a real need, build it through the release workflow so it is signed.
- **An unsigned release that is meant to run:** do not add it to the frozen list. Release again, let the new build pass staging, and promote that ([release a version](release-a-version.md)). Rolling back to a listed tag is allowed until the list ends.
- **Cause 5, credentials:** wait up to 6 hours for the node to rewrite `ecr-cred`, or on the node run `sudo /usr/local/sbin/kaval-gitops-bootstrap` by hand over Session Manager. Do not paste a token into a secret by hand.
- **Cause 6, the checker is down:** `flux reconcile helmrelease kyverno -n flux-system --reset` is the first remedy for a stuck release ([ADR-0076](../adr/0076-a-stalled-flux-release-is-reset-not-rebuilt.md)). While it only watches, nothing is blocked, so there is no emergency; once it refuses (`KAV-142`) the off-switch is in that story's runbook section.
- **Turn the check off** (only if it is itself breaking something, and only once it refuses): that is a reviewed change to `deploy/image-policy/image-validating-policy.yaml` back to `validationActions: [Audit]` and `failurePolicy: Ignore`, through a pull request.

## Do not

- Do not add a tag to the frozen list to make an alert stop. The list is the releases that existed before signing; a new entry is a new exemption and needs an ADR line.
- Do not delete the policy or the `kyverno` namespace to "see if it helps": removing the policy removes the check, and nobody is told.
- Do not paste the registry address or account number into an issue or pull request; it appears in the Event text. Replace it with `<registry>`.
- Do not set `failurePolicy: Fail` while it only watches: a dead checker would then block every Kaval pod.
