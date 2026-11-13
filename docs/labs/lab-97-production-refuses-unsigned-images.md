# Lab 97 — Production refuses unsigned images

**Phase:** 8 (hardening) · **Story:** `KAV-143` · **Decision:** [ADR-0095](../adr/0095-production-refuses-unsigned-images-from-its-next-start-up.md) · **Cost:** $0 for Parts 1-3 (files, tests and a read-only plan); about 5-10 cents for Part 4, which rides on a production start-up that is happening anyway

## What you will build

[Lab 96](lab-96-the-cluster-refuses-unsigned-images.md) made staging refuse any Kaval image that is not signed by `release.yml` on `main` (and not on the frozen list). This lab gives production the **same** checker. Nothing about the policy changes; the work is making production match staging exactly, and proving it on production's real server, which also runs the language model.

You need the repository, Python with the project's dev tools, `terraform`, `helm` and `AWS_PROFILE=kaval`. Parts 1-3 create nothing in AWS. Part 4 needs a typed go from the owner for each live step (a production start-up, the apply, the registry change).

## Part 1 — What changes in production, file by file

| File | Change | Why |
|---|---|---|
| `deploy/gitops/prod/checker-source.yaml`, `checker-helmrelease.yaml`, `image-policy-sync.yaml` | new: copies of staging's three | Kyverno itself, pinned by chart version and digest, and Flux's object that applies the policy. Kustomize refuses files outside its own folder (`KAV-51`), so they are copies |
| `deploy/gitops/prod/image-policy-sync.yaml` | `kaval_namespace: kaval-prod` | the only line that differs from staging |
| `deploy/gitops/prod/kustomization.yaml` | lists the three | Flux builds this folder |
| `deploy/environments/prod/values.yaml` and the inline copy in `deploy/gitops/prod/helmrelease.yaml` | `imageChecker: {enabled: true}` | Prometheus scrapes the checker and loads the two alert rules |
| `infra/envs/prod/main.tf` | `image_checker_enabled = true` | the server's start-up script copies the registry login into the checker's namespace; the 6-hourly timer refreshes it |

`deploy/image-policy/image-validating-policy.yaml` is **not** changed in behaviour: the same signer, the same frozen list, the same `Deny` + `Fail`, the same 30-second answer time, the same Kaval-namespace-only scope.

## Part 2 — The tests, and making them fail

```bash
python -m pytest scripts/dev/test_image_policy.py scripts/ops/test_checker_proof.py -q
```

`test_production_gets_nothing_before_its_own_story` is replaced **on purpose** by parity tests. Break production six ways, run the tests after each, and put the file back (`git checkout <file>`):

| Break | Test that goes red |
|---|---|
| a different Kyverno chart version in production's copy | `test_production_and_staging_run_the_very_same_checker` |
| production's policy pointed at `kaval-staging` | the same test |
| `image_checker_enabled = false` in `infra/envs/prod/main.tf` | `test_the_production_server_hands_the_checker_its_login_and_the_timer_refreshes_it` |
| the 6-hourly timer slowed to 30 hours (the login would go stale) | the same test |
| `imageChecker` removed from production's values | `test_prometheus_watches_the_checker_in_staging_and_production` |
| `image-policy-sync.yaml` left out of the kustomization | `test_production_lists_the_checker_files` |

Result on 2026-11-13: all six went red; the restored files are green. The whole repository's tests passed too (1,740 passed, 360 skipped).

Also render production's chart: `helm template kaval deploy/charts/kaval -f deploy/environments/prod/values.yaml` now contains the `kyverno` scrape job and the image-checker rules.

## Part 3 — Read production's plan (read-only)

```bash
export AWS_PROFILE=kaval
scripts/ops/tf-init.sh prod
cd infra/envs/prod && terraform plan -lock=false     # production's own *.tfvars files must be in this folder
```

Result on 2026-11-13: **0 to add, 2 to change, 0 to destroy**: the launch template (the start-up script, and a newer base machine image that was already waiting from `KAV-96`) and the node group that points at it. Production's node group is parked at 0, so nothing running changes. **Never apply this to production without the owner's go.**

The server's start-up script has a size limit (16,384 bytes once compressed); the test that guards it (`test_the_node_start_up_script_still_fits_the_ec2_limit`) measures the template, not one environment, and staging's fully-rendered script is already the largest, so production's passes.

## Part 4 — The live proof, on production's next start-up

**Status: not run yet.** It needs the owner's typed phrase for the start-up and the apply (the usual `go production start-up`, then `apply production`), and, in the same session, a typed go for the registry change in step 2. Nothing in it is a promotion.

Before starting: this pull request merged, `main` clean and in sync, no other change waiting.

1. **Start production** as usual (`make up`; the plan in Part 3 is what is applied) and let it come up. `make prod-smoke` must pass first: if production is not healthy, the checker is not the thing to look at.
2. **Give one image an unsigned second name** (a registry change in the one shared registry; the planted name is removed in step 9):
   ```bash
   python scripts/ops/checker_proof.py prod plant --yes
   ```
3. **Read the state, with the model loaded** (read-only; do it again after the proof):
   ```bash
   python scripts/ops/checker_proof.py prod state
   ```
   Expect the policy `ready=true actions=[Deny] failurePolicy=Fail timeout=30`, every Kaval pod `Running`, one checker pod `Running`, both hooks present, a fresh `ecr-cred` in `kyverno`. Write down the checker's memory, and the server's `free -m` and `top` figures (the stop rule is in the ADR, point 7).
4. **Signed passes:** `python scripts/ops/checker_proof.py prod signed --yes` → `pod/proof-signed created` and `signed pod: 1/1 Running` (or `0/1 ContainerCreating` while the image downloads: created is the answer).
5. **Unsigned is refused (the negative case):** `python scripts/ops/checker_proof.py prod unsigned --yes` → an error from the API server naming `kaval-images-signed`, the pod does not exist, and the `fail` counter is 1 or more.
6. **Production's own pods all passed:** read in step 3; they run `sha-d1058eb`, on the frozen list.
7. **The alarms exist:** `python scripts/ops/checker_proof.py prod alerts` → both rules loaded and `ok`. (A refusal also raises `UnsignedImageSeen` within a few minutes; the Slack card is the proof the route works.)
8. **Read the state again** and compare the memory.
9. **Clean up:** `python scripts/ops/checker_proof.py prod clean --yes`, then `python scripts/ops/checker_proof.py prod unplant --yes`, then park production as usual (`make down`).

Not repeated here: the hard kill of the checker and the emergency off-switch, which Lab 96 Part 4 proved on staging with the same files and Kyverno release.

**If a step fails:** do not push on. The runbook ([image flagged by the cluster check](../runbooks/image-flagged-by-the-cluster-check.md)) has the causes; the normal off-switch is a reviewed pull request back to `[Audit]` + `Ignore`, the emergency one is deleting Kyverno's two hooks. A pod proving nothing is worse than none: record what happened, and what it means for the decision, in the journal.

## Things worth knowing

- **Why no watch-only first:** staging already did the watching and the refusing; production's pin is on the frozen list, so it cannot be locked out by its own checker; and a second start-up costs the same as the first. The proof's stop rule (memory) covers the one thing staging could not tell us.
- **Why a throwaway pod rather than a promoted signed release:** it proves the same path (Sigstore reachable from production's server, the login valid, the signature found) without an image change to carry it. The real promotion of a signed release is the pass case `KAV-139` waits for, and has to happen before the frozen list ends (2026-12-31).
- **A refused rollback is a surprise in a hurry.** After the list ends, `rollback.yml` can only go back to a signed release; before then, to a listed one. The runbook says so.
