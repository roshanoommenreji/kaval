# Lab 96 — The cluster refuses unsigned images (and what a dead checker means)

**Phase:** 8 (hardening) · **Story:** `KAV-142` · **Decision:** [ADR-0094](../adr/0094-the-cluster-refuses-unsigned-images-on-staging-and-fails-closed.md) · **Cost:** $0 for Parts 1-3 (a laptop cluster, read-only against ECR); about $0.05 planned and $0.15 actual for Part 4 (one staging session) · **Time:** 45 minutes for Parts 1-3

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

## Part 4 — The live proof on staging (his typed go)

The plan, about $0.05, about an hour of staging (it ran on 2026-11-13; the result is below):

1. `make staging-up` (if it stops on "insufficient t4g.medium capacity", re-run once; Lab 95). Expect: the policy `READY true` and showing `Deny`, all 9 Kaval pods `Running` (staging runs `sha-d1058eb`, which is on the frozen list), both Flux objects Ready.
2. A **signed** release that is not listed (`sha-152b59e`) starts; the counter `result="pass"` rises.
3. A planted **unsigned** tag is **refused** with `denied the request ... kaval-images-signed`; a `PolicyViolation` Event; `UnsignedImageSeen` fires. Remove the planted tag afterwards.
4. **Crash the checker** (`kubectl -n kyverno delete pod -l app.kubernetes.io/component=admission-controller --grace-period=0 --force`) and create a Kaval pod at once: refused with `failed calling webhook`; a pod in another namespace is not. The checker comes back and the same pod starts.
5. **Roll back to a frozen tag** (`sha-d1058eb`): allowed.
6. Tear down; sweep the account.

**Result on 2026-11-13** (his `go staging proof`; staging destroyed afterwards, account sweep clean; **about 15 cents**, not the planned 5, because staging stayed up about 3.5 hours: the build needed a second capacity workaround and I then waited between steps):

| Step | What we saw |
|---|---|
| 1. State | Policy `READY true`, `Deny`, `Fail`, 30 s. All 9 Kaval pods `Running` (staging pins `sha-d1058eb`, on the frozen list). Both Kyverno hooks present. Checker **48 MiB**. |
| 2. Signed, not listed | A pod from `sha-152b59e` started; counter `result="pass"` = 1 (`policy_validation_mode="Deny"`). |
| 3. Unsigned, not listed | A pod from the planted `unsigned-trial` tag was **refused** (`denied the request: Policy kaval-images-signed failed ...`), no pod exists, counter `result="fail"` = 1, a `PolicyViolation` Event reads `fail (blocked)`. The planted tag was removed afterwards; the real `sha-791c44d` is untouched. |
| 4. Rollback and a real pod | A pod from the frozen `sha-d1058eb` started. A real gateway pod, deleted, was recreated by its Deployment through the live checker (`1/1 Running`, no `FailedCreate`). |
| 5. Crashed checker | Hard-killed (`--grace-period=0 --force`); a Kaval pod created at once was **refused**: `failed calling webhook ... no endpoints available for service "kyverno-svc"`. A pod in another namespace was created. The checker came back by itself, the same pod then started, and the unsigned image was refused again. |
| 6. Emergency off-switch | Checker killed, a Kaval pod refused; both hooks deleted by hand; the pod **started**; the checker re-registered both hooks by itself (within about 6 s) and the unsigned image was refused again. |
| Alert | `UnsignedImageSeen` was `firing` about a minute after a second refusal (the counter went 1 to 2). |
| Teardown | 86 items destroyed, the data disk deleted, nothing tagged `Env=staging` left; the full account sweep was clean and production was untouched (database stopped, server group 0). |

Things worth knowing that the plan did not predict:

- **The capacity problem was worse and different this time.** The first build stopped (no `t4g.medium` in `ap-south-1b`); the one re-run stopped again (`ap-south-1c`) after destroying the empty server group. But AWS's own Auto Scaling then started a node in another zone by itself two minutes later, so the node came up although Terraform had given up. **Do not re-run `make staging-up` in that state: Terraform would destroy the working node as a failed one.** Use the node that is there, and tear down normally afterwards. Because Terraform stopped before creating the rest, the node's database-seed step waited its full 10 minutes for a marker file and then carried on with an **empty database** (non-fatal, by design); the proof does not need data.
- **A refusal right after a checker restart can be invisible to `UnsignedImageSeen`.** The counter lives in the checker, so a restart resets it to 0; Prometheus keeps the same series (its address is the Service's, not the pod's). In the proof the count before the crash was 1 and a refusal right after the restart made it 1 again, so `increase()` saw nothing; the next refusal (1 to 2) fired the alert at once. The refusal itself, the `PolicyViolation` Event and the pod error are unaffected, so nothing unsigned ever starts; only the alert is blind in that narrow case. Written down in ADR-0094, not fixed.
- **My steps took hours of wall-clock time** (node time 08:57 to 11:56) because I ended turns waiting for notifications; that is where the extra cost came from. A future proof should be run in one sitting.


## Part 5 — The off-switch drill

Written in the [runbook](../runbooks/image-flagged-by-the-cluster-check.md) (the normal way: a pull request back to `Audit` + `Ignore`; the emergency way: delete both hooks). Part 3 tried the emergency way on the laptop; Part 4 should try it once on staging before production gets the same (`KAV-143`).
