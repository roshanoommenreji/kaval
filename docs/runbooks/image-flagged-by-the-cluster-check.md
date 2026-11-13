# An image was flagged by the cluster's signature check

The cluster has a checker (Kyverno, `KAV-140`, [ADR-0093](../adr/0093-the-cluster-checks-image-signatures-with-kyverno-watching-first.md)) that looks at every Kaval pod as it is created and asks whether the image was signed by this repository's release workflow on `main`. **On staging it refuses** (`KAV-142`, [ADR-0094](../adr/0094-the-cluster-refuses-unsigned-images-on-staging-and-fails-closed.md)): a pod whose image fails is not created, and if the checker is down or cannot answer, new Kaval pods wait too. Production refuses the same way from its next start-up (`KAV-143`, [ADR-0095](../adr/0095-production-refuses-unsigned-images-from-its-next-start-up.md)); where this says "the environment", the namespace is `kaval-staging` or `kaval-prod`, and Flux's policy object is `kaval-staging-image-policy` or `kaval-prod-image-policy`. This is what to do when it speaks up, when it goes quiet, or when it is in your way.

For a person, no automatic fix is proposed. The checker reports; it does not change anything, and the agent must not either.

## Signals

- Alert **`UnsignedImageSeen`**: a Kaval image was refused (on production: started anyway) because it has no valid signature and is not on the frozen list. A refusal caused by an unreachable Sigstore counts the same way; the Event text says which
- Alert **`ImageCheckerDown`**: Prometheus cannot reach the checker for five minutes. On staging that means **new Kaval pods cannot start** (running ones are untouched); on production, that we are blind to unsigned images
- A cluster Event `PolicyViolation` naming `kaval-images-signed`: `kubectl get events -n kaval-staging --field-selector reason=PolicyViolation`
- (Staging) a Deployment that cannot make its pod: `kubectl get events -n kaval-staging --field-selector reason=FailedCreate` shows `denied the request ... kaval-images-signed` (an image problem) or `failed calling webhook` (the checker is down or too slow)

## Likely causes

1. **A person started an image by hand**, from a tag nobody released (for example a debugging copy). Expected only in a trial; in real use it is the case the checker exists for
2. **A release that was built before signing existed (before 2026-11-13) and is not on the frozen list**, for example a rollback to an older version that was not on the passed-staging record when the list was written. The list is the 19 releases on the record then
3. **The frozen list's end date (2026-12-31) has passed** and the list was removed, but production or staging still runs an unsigned release. Release again; do not extend the list without a new ADR line
4. **A release run stopped before it signed** (Sigstore or ECR was unreachable): the image exists, has no signature, and the release run shows a failed **sign the image** step
5. **The checker cannot read the signature**, so every Kaval image looks unsigned: the `ecr-cred` secret in the `kyverno` namespace is missing or expired (the node rewrites it every 6 hours), or Sigstore's public services are unreachable from the node
6. **The checker is down** (`ImageCheckerDown`): its pod is not running (out of memory, a failed Helm release, the Flux object for it stuck). On staging, `failed calling webhook` is the sign
7. **Sigstore is unreachable from the node**: the refusal text carries `failed to initialize TUF client` or a connection error. Even properly signed images are refused until Sigstore answers again (measured, [ADR-0094](../adr/0094-the-cluster-refuses-unsigned-images-on-staging-and-fails-closed.md) point 4). Check <https://status.sigstore.dev>; nothing to fix on our side, use the off-switch below only if a pod must start now

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
   - `flux get kustomization kaval-staging-image-policy -n flux-system` (or `kaval-prod-image-policy` on production) (Ready)
   - on a live node, `python scripts/ops/checker_proof.py <prod|staging> state` reads most of the above in one go (read-only; [Lab 97](../labs/lab-97-production-refuses-unsigned-images.md))

## Remediation

- **An image nobody released:** delete the pod, find who started it and why. If it was a real need, build it through the release workflow so it is signed.
- **An unsigned release that is meant to run:** do not add it to the frozen list. Release again, let the new build pass staging, and promote that ([release a version](release-a-version.md)). Rolling back to a listed tag is allowed until the list ends.
- **Cause 5, credentials:** wait up to 6 hours for the node to rewrite `ecr-cred`, or on the node run `sudo /usr/local/sbin/kaval-gitops-bootstrap` by hand over Session Manager. Do not paste a token into a secret by hand.
- **Cause 6, the checker is down:** `flux reconcile helmrelease kyverno -n flux-system --reset` is the first remedy for a stuck release ([ADR-0076](../adr/0076-a-stalled-flux-release-is-reset-not-rebuilt.md)). Running pods are fine; only new Kaval pods wait, so there is time to fix it properly. A clean restart of the checker removes its own hook for a few seconds (3 to 30 s measured) and then puts it back by itself.
- **A refusal that did not raise the alert:** right after a checker restart, one refusal can leave `UnsignedImageSeen` silent (the counter restarts at 0 and looks unchanged); the refusal itself, the `PolicyViolation` Event and the pod's `FailedCreate` error are still there, so look at those. The next refusal fires the alert.
- **Turn the check off, the normal way** (it is itself breaking something and there is time): a reviewed pull request that changes `deploy/image-policy/image-validating-policy.yaml` back to `validationActions: [Audit]` and `failurePolicy: Ignore`; Flux applies it.
- **Turn the check off, the emergency way** (the checker is down or Sigstore is out, and a legitimate pod must start now; the tried and tested sequence is in `scripts/dev/checker_trial.sh`):
  1. Delete Kyverno's two hooks, which is what makes the API server stop asking it: `kubectl delete validatingwebhookconfiguration kyverno-resource-validating-webhook-cfg` and `kubectl delete mutatingwebhookconfiguration kyverno-resource-mutating-webhook-cfg` (both: with only one gone, the other still blocks). Flux does not own these two, so it does not put them back
  2. Start what you need. Anything started now was **not checked**: write down what and why, and check it by hand afterwards (`python scripts/release/promote.py signatures --tag <tag>`)
  3. When the checker is healthy again it puts both hooks back by itself (3 to 13 s measured). Confirm an unsigned test pod is refused again. If the checker stays down, fix that (cause 6); the check is off until you do

## Do not

- Do not add a tag to the frozen list to make an alert stop. The list is the releases that existed before signing; a new entry is a new exemption and needs an ADR line.
- Do not delete the policy or the `kyverno` namespace to "see if it helps": removing the policy removes the check, and nobody is told.
- Do not paste the registry address or account number into an issue or pull request; it appears in the Event text. Replace it with `<registry>`.
- Do not set `failurePolicy: Fail` on an environment where the checker only watches: a dead checker would then block every Kaval pod for a check that blocks nothing. Staging and production both run `Deny` + `Fail` and a test pins it. Because the policy file is shared, going back to `[Audit]` + `Ignore` is a change for both at once (a reviewed pull request); there is no per-environment switch on purpose.
- Do not roll production back to a build that is neither signed nor on the frozen list: it will be refused. Listed tags can be rolled back to until 2026-12-31; after that only signed releases, so production should be on a signed release before then.
- Do not widen the scope beyond the Kaval namespace: that scope is what makes `Fail` safe (the checker's own pods, Flux and the system pods can never be waiting on the checker).
- Do not leave the hooks deleted once the emergency is over: that is the check off, quietly. Confirm they are back (`kubectl get validatingwebhookconfiguration kyverno-resource-validating-webhook-cfg`).
