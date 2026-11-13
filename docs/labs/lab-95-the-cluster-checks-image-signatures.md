# Lab 95 — The cluster checks image signatures (watching first)

**Phase:** 8 (hardening) · **Story:** `KAV-140` · **Decision:** [ADR-0093](../adr/0093-the-cluster-checks-image-signatures-with-kyverno-watching-first.md) · **Cost:** $0 for Parts 1-4 (a laptop cluster, read-only against ECR); about $0.05 for Part 5 (one staging session) · **Time:** 45 minutes for Parts 1-4

> **Superseded in part by [Lab 96](lab-96-the-cluster-refuses-unsigned-images.md) (`KAV-142`):** on staging the policy now refuses (`Deny` + `Fail`). This lab is the watch-only chapter and stays as the record of how the checker was built and first proven.

## What you will build

A checker inside the cluster that looks at every Kaval pod as it is created and asks: does this image carry a signature made by this repository's release workflow on `main`? It is **watch-only**: it reports and refuses nothing. Refusing is `KAV-142`.
The images running today were all built before signing existed, so a frozen, dated list of those 19 releases keeps them (and rollback) quiet until 2026-12-31.

You need a laptop with Docker, `k3d`, `kubectl`, `helm`, Python, and an AWS login that can read the Kaval image repositories (`AWS_PROFILE=kaval`). Nothing is created in AWS.

## Part 1 — Read the change

| File | What it is |
|---|---|
| `deploy/image-policy/image-validating-policy.yaml` | The rule, in Kyverno's language. Read its header first. |
| `deploy/gitops/staging/checker-source.yaml`, `checker-helmrelease.yaml` | Flux installs Kyverno (admission controller only, six images pinned by digest). |
| `deploy/gitops/staging/image-policy-sync.yaml` | Flux applies the rule once Kyverno exists, filling in the registry address and the namespace. |
| `infra/modules/node/user_data.sh.tftpl`, `variables.tf`, `infra/envs/staging/main.tf` | The node writes the registry login into the `kyverno` namespace (only where `image_checker_enabled` is true). |
| `deploy/charts/kaval/files/kaval-image-checker.rules.yaml`, `templates/prometheus.yaml` | Prometheus watches it; two alerts. |

Two things to notice. The registry address is **not** in any file (it holds the account number): Flux reads it from a ConfigMap the node makes. And `validationActions: [Audit]` and `failurePolicy: Ignore` are a pair: while it only watches, a down checker must never stop a Kaval pod.

## Part 2 — The tests

```bash
python -m pytest scripts/dev/test_image_policy.py -q     # 27 tests, no cluster needed
bash scripts/dev/rules_test.sh                            # the alert rules, with promtool (needs Docker)
```

They pin: the signer is exactly the one the promotion gate uses and never a pattern; the frozen list is on the passed-staging record, holds nothing from the signed era and has an end date that fails the build once it passes; the signature check runs before the list is consulted; Kyverno and all six of its images are pinned; only the admission program is on; production is untouched; no file holds the account number.

## Part 3 — Make sure the tests can fail

Break the policy five ways, run the tests after each, then put the file back (`git checkout deploy/image-policy`):

1. Change `subject:` to `subjectRegExp:` and the branch to `.*` → red (the signer is no longer exact).
2. Swap the order of the two halves of the expression (list first) → red.
3. Add `'sha-ffffff0',` to the list → red (not on the record).
4. Set `kaval.dev/frozen-list-ends` to a past date → red.
5. Change `[Audit]` to `[Deny]` while `failurePolicy` is `Ignore` → red.

Result on 2026-11-13: all five went red, and the restored file is green.

## Part 4 — Ask the real question, on a laptop cluster

```bash
SIGNED_TAG=sha-152b59e scripts/dev/checker_trial.sh
```

It makes a throwaway cluster (k3s 1.32; Docker Desktop's engine is too old for the newest kubelet), installs Kyverno exactly as Flux does, applies the **real policy file** with your registry filled in, and asks the API server six questions with a server-side dry run (nothing is created, no image is pulled). It deletes the cluster at the end.

Result on 2026-11-13, against the real images:

```
  PASS  signed release in a Kaval namespace -> quiet
  PASS  frozen old release (sha-d1058eb, unsigned) -> quiet
  PASS  unsigned release NOT on the frozen list -> flagged
  PASS  the same unsigned image in an init container -> flagged
  PASS  the same unsigned image in another namespace -> quiet
  PASS  signed release, policy expects ci.yml -> flagged        (the control)
kyverno-admission-controller  32m  68Mi
TRIAL PASSED
```

The control is the important one: if a signed image also passes when the policy expects the wrong workflow, the checker is not checking anything. It is flagged, so the quiet answer above is a real verification.

**What was found on the way** (all fixed in the files you read in Part 1):

- The other candidate, Sigstore **policy-controller 0.13.1, cannot read our signatures** (`bundle support for image signatures is not yet implemented`). That is why it is Kyverno.
- With the frozen list consulted first, Kyverno said "no policy performed a signature check on it" about a listed image. Check first, list second.
- In watch-only mode Kyverno still writes a cluster Event (`PolicyViolation`) and a counter (`kyverno_image_validating_policy_results_total`) without its reports controller, which is why that controller stays off.
- Kyverno's default scope is every namespace and every pod; a down checker would then hold up the cluster's own pods. The policy is limited to the Kaval namespace.

## Part 5 — The proof on staging (on the owner's typed go)

Staging's pods all run releases on the frozen list, so a healthy staging reports nothing. The live proof therefore plants one image the checker must notice. Each step says what to expect.

1. `make staging-up`. Expect: Flux installs Kyverno (`kubectl -n kyverno get pods`: one admission pod `Running`), `kubectl get imagevalidatingpolicies` shows `READY true`, the Flux object `kaval-staging-image-policy` is Ready (it may fail for a minute or two first, until Kyverno has installed its kinds), the `ecr-cred` secret exists in `kyverno`, and every Kaval pod is `Running` as before.
2. **Memory**, measured on the real node: `kubectl top pod -n kyverno` and the node's total. Write both in the result.
3. **A signed release that is not on the list passes:** a one-off pod from `sha-152b59e` in the Kaval namespace is quiet (no Event; the `pass` counter rises).
4. **An unsigned image that is not on the list is noticed, and not blocked.** Make a tag of an old unsigned image without copying any data, run a pod from it, then remove the tag:
   ```bash
   aws ecr batch-get-image --repository-name kaval/gateway --image-ids imageTag=sha-791c44d \
     --query 'images[0].imageManifest' --output text > /tmp/m.json
   aws ecr put-image --repository-name kaval/gateway --image-tag unsigned-trial --image-manifest file:///tmp/m.json
   kubectl -n kaval-staging run unsigned-trial --image=<registry>/kaval/gateway:unsigned-trial --restart=Never --command -- sleep 120
   ```
   Expect: the pod **starts** (watch-only), `kubectl get events -n kaval-staging` shows a `PolicyViolation` naming `kaval-images-signed`, the `fail` counter rises, and `UnsignedImageSeen` fires in Prometheus within about two minutes. Then `kubectl delete pod unsigned-trial` and `aws ecr batch-delete-image --repository-name kaval/gateway --image-ids imageTag=unsigned-trial`.
5. **A dead checker is noticed and does not block:** `kubectl -n kyverno scale deploy kyverno-admission-controller --replicas=0`, start any pod in the Kaval namespace (it starts), wait five minutes for `ImageCheckerDown`, then scale back to 1.
6. `make staging-down`, then the account sweep.

**Result on 2026-11-13** (his `go staging proof`; morning UTC; staging destroyed afterwards, account sweep clean; about 5-8 cents):

| Step | What we saw |
|---|---|
| 1. Build | Flux installed Kyverno (one admission pod `Running`), `kaval-images-signed` showed `READY true`, both Flux objects (`kaval-staging`, `kaval-staging-image-policy`) were Ready, `ecr-cred` existed in `kyverno`, and all 9 Kaval pods were `Running`. |
| 2. Memory | Checker **48 MiB** at rest and **44 MiB** after it had done its work, 6-7m CPU. The whole node: 2,377 MiB (62%) with the checker idle, 2,355 MiB (61%) at the end, of about 3.8 GiB. The laptop figure was 68-75 MiB; the real node is lower. |
| 3. Signed, not listed | A pod from `sha-152b59e` started, no Event, and `kyverno_image_validating_policy_results_total{result="pass"}` went to 1 (`policy_validation_mode="Audit"`). It also shows the node can reach the Sigstore services at admission time. |
| 4. Unsigned, not listed | A pod from the planted `unsigned-trial` tag **started** (watch-only) and a `PolicyViolation` Event named `kaval-images-signed` and the runbook; the `fail` counter went to 1; the target `kyverno` was `up` and `UnsignedImageSeen` was `firing` when read about two minutes later. The tag was removed afterwards; the real `sha-791c44d` tag was untouched. |
| 5. Dead checker | Admission controller scaled to 0 at 06:48:02Z; a new pod still **started**; `ImageCheckerDown` was `firing` when read at 06:54:10Z. Scaled back to 1, rollout fine, policy `READY true`. |
| 6. Teardown | 92 items destroyed, the data disk deleted, nothing tagged `Env=staging` left; the full account sweep was clean and production was untouched. |

Things worth knowing that the plan did not predict:

- **The first build failed on capacity:** AWS had no `t4g.medium` in one availability zone (ap-south-1b) at that moment, so the app server group could not start. The database server and network were already built. Re-running `make staging-up` once (plan: 7 to add, 1 to destroy, the empty failed server group replaced) succeeded. Nothing was lost. If it happens again, re-run once; if it persists, wait and try later.
- **Kyverno rewrites the image to its digest** in the pod (`kaval/gateway:sha-152b59e@sha256:...`). That is expected and is why the frozen list is matched on the tag, which the digest form still carries.
- **While the checker is down, `UnsignedImageSeen` disappears with it.** Only `ImageCheckerDown` speaks. That is another reason the second alert exists, and `KAV-142` must decide what a dead checker means once it refuses.
- Two older alerts, `KavalCostPullFailing` and `KavalWasteScanFailing`, were `pending` on the fresh staging. They belong to the cost and waste jobs, not the checker, and were not investigated here.


## What to take away

- **A check that cannot say no is not a check.** The wrong-signer control is what turns "it stayed quiet" into "it verified".
- **Watch before you refuse.** The same policy file will refuse later by changing two settings; until a clean report says it would not refuse anything legitimate, it only watches.
- **Say what you exempt, say why, and give it an end date.** The frozen list is not a loophole: it is exactly the releases the record already holds, it can only shrink, and the build goes red when its time is up.
- **Measure, do not guess.** The one program we need is 68-75 MiB on the laptop cluster; the live number comes from Part 5.
