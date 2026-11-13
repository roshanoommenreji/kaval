# Lab 96 — The cluster refuses unsigned images (and what a dead checker means)

**Phase:** 8 (hardening) · **Story:** `KAV-142` · **Decision:** [ADR-0094](../adr/0094-the-cluster-refuses-unsigned-images-on-staging-and-fails-closed.md) · **Cost:** $0 for Parts 1-3 (a laptop cluster, read-only against ECR); about $0.05 for Part 4 (one staging session, needs the owner's typed go) · **Time:** 45 minutes for Parts 1-3

## What you will build

[Lab 95](lab-95-the-cluster-checks-image-signatures.md) built a checker that only watches. This lab makes it refuse on staging: a Kaval pod whose image has no valid signature from `release.yml` on `main`, and is not on the frozen list, is **not created**. The harder half is the questions a refusing checker raises: what happens when it is down, slow, or cannot reach the public signing services? Every answer below was measured, not assumed.

You need the same laptop as Lab 95 (Docker, `k3d`, `kubectl`, `helm`, Python, `AWS_PROFILE=kaval`). Nothing is created in AWS.

## Part 1 — Read the change

One file changes in behaviour: `deploy/image-policy/image-validating-policy.yaml`.

| Setting | Before (Lab 95) | Now | Why |
|---|---|---|---|
| `validationActions` | `[Audit]` | `[Deny]` | refuse instead of report |
| `failurePolicy` | `Ignore` | `Fail` | a checker that is down must not mean "anything goes" |
| `webhookConfiguration.timeoutSeconds` | default 10 | `30` | the first check after a start-up took 8.3 s; with `Fail`, a timeout is a refusal |

What makes `Fail` safe is the scope, unchanged: the Kaval namespace only. The checker's own pods, Flux and the system pods are never waiting on it, so a dead checker cannot stop its own repair.

## Part 2 — The tests, and making them fail

```bash
python -m pytest scripts/dev/test_image_policy.py -q     # 27 tests, no cluster needed
```

`test_it_refuses_and_fails_closed_together` replaces Lab 95's watch-only test **on purpose**: it pins `Deny`, `Fail`, the 30-second answer time and the Kaval-only scope. Break the policy four ways, run the tests after each, put the file back (`git checkout deploy/image-policy`):

1. `[Deny]` → `[Audit]`: red
2. `Fail` → `Ignore`: red
3. add `"kube-system"` to the namespace list: red (two tests)
4. `timeoutSeconds: 30` → `10`: red

Result on 2026-11-13: all four went red; the restored file is green (27 passed).

## Part 3 — Ask the real questions, on a laptop cluster

```bash
SIGNED_TAG=sha-152b59e scripts/dev/checker_trial.sh      # about 8 minutes; KEEP=1 leaves the cluster up
```

A throwaway cluster, Kyverno installed as Flux installs it, the **real policy file** with your registry filled in, 21 answers from the API server (server-side dry run; nothing is created, no image pulled).

Result on 2026-11-13, against the real signed images:

| Question | Answer |
|---|---|
| Signed release in the Kaval namespace | allowed |
| Frozen old release (unsigned) | allowed |
| Unsigned release not on the list (also in an init container) | **refused** |
| The same unsigned image in another namespace | allowed (out of scope) |
| Control: policy expects the wrong signer (`ci.yml`) | **refused**, even the signed release (took effect after 3 s) |
| Checker **crashed** (killed hard, its hook stays registered) | new Kaval pods **refused**, signed or not; pods elsewhere and in `kube-system` untouched |
| Emergency off-switch: delete both of Kyverno's hooks while it is down | allowed; Kyverno re-registers them by itself (3 s) and the unsigned image is refused again |
| Checker **stopped cleanly** (scale to zero, rolling update, node shutdown) | **allowed** while it is gone: Kyverno removes its own hook (back after 3-16 s) |
| After either restart | signed allowed, unsigned refused: it resumes by itself |
| **Sigstore unreachable** (its addresses point nowhere, for the checker only) | **refused, even the signed release**; allowed again once reachable |
| Memory | 44-51 MiB, 18-62m CPU |

### Things worth knowing that the plan did not predict

- **A cleanly stopped Kyverno deletes its own hook, a crashed one does not.** So "the checker is down" is two different cases. Crash, out-of-memory kill, node loss: fails closed, as chosen. A planned restart: open for a few seconds. This is Kyverno's design and cannot be switched off; it is accepted in ADR-0094 (point 5) and written down so nobody finds it by surprise.
- **Two hooks, not one.** Kyverno registers a validating hook and a mutating one (it rewrites the image to its digest). The emergency off-switch has to delete both; with only the validating one gone, the mutating one still fails the pod. The first version of the trial found this.
- **My first "checker down" test did not test anything.** Pointing the Service at nothing leaves the API server's already-open connection working, so the checker still answered. A real kill (`--grace-period=0 --force`) is the honest simulation.
- **The default 10-second answer time was too tight** (8.3 s for the first check after a start), hence 30.
- **Sigstore down refuses properly signed images**, with the real reason in the Event (`failed to initialize TUF client`), counted as a `fail`, so `UnsignedImageSeen` fires. Embedding Sigstore's trust data would remove the dependency but a stale copy would one day refuse every new release with no outage to blame, so it is deliberately not done (ADR-0094 point 4).
- **One flake, not explained.** In one of the runs the wrong-signer control answered "allowed" (the policy change had not reached the hook after 8 s); in the final run it took effect after 3 s. The control now asks every 3 s for up to 90 s and prints how long it took, and still fails if it never takes effect.

## Part 4 — The live proof on staging (needs his typed go)

Not run yet. The plan, about $0.05, about an hour of staging:

1. `make staging-up` (if it stops on "insufficient t4g.medium capacity", re-run once; Lab 95). Expect: the policy `READY true` and showing `Deny`, all 9 Kaval pods `Running` (staging runs `sha-d1058eb`, which is on the frozen list), both Flux objects Ready.
2. A **signed** release that is not listed (`sha-152b59e`) starts; the counter `result="pass"` rises.
3. A planted **unsigned** tag is **refused** with `denied the request ... kaval-images-signed`; a `PolicyViolation` Event; `UnsignedImageSeen` fires. Remove the planted tag afterwards.
4. **Crash the checker** (`kubectl -n kyverno delete pod -l app.kubernetes.io/component=admission-controller --grace-period=0 --force`) and create a Kaval pod at once: refused with `failed calling webhook`; a pod in another namespace is not. The checker comes back and the same pod starts.
5. **Roll back to a frozen tag** (`sha-d1058eb`): allowed.
6. Tear down; sweep the account.

Result: _to be filled in after the staging session._

## Part 5 — The off-switch drill

Written in the [runbook](../runbooks/image-flagged-by-the-cluster-check.md) (the normal way: a pull request back to `Audit` + `Ignore`; the emergency way: delete both hooks). Part 3 tried the emergency way on the laptop; Part 4 should try it once on staging before production gets the same (`KAV-143`).
