# Lab 61 — Production comes up with the Phase 6 parts, and removal is switched on last

**Phase:** 6 · **Story:** `KAV-98` (with the `KAV-90` production checks) · **Time:** about 3 hours · **Cost:** production
at about $0.05 an hour while it is up (from 11:52 UTC on 2026-10-21); everything else $0 extra

Production had been parked since Phase 5. The Phase 6 parts (cost reader `KAV-92`, tags `KAV-93`, waste scan `KAV-94`,
safe removal `KAV-95`) were proven on staging. This lab brings production up with them in the safest order, following
[the production bring-up runbook](../runbooks/prod-rollout.md). The order is the point: **read first, delete last**.

## Prerequisites

- `AWS_PROFILE=kaval`, Terraform connected (`scripts/ops/tf-init.sh prod`), `gh`, helm, the laptop's Session Manager plugin
- A build that passed staging and was recorded (`make staging-smoke`), plus the old-database drill of
  [Lab 54](lab-54-a-new-version-starts-by-itself-on-an-old-database.md) repeated with that build
- Production parked: the app server group at 0 (`infra/envs/prod/node.auto.tfvars` says
  `app_node_desired_capacity = 0`) and the database server stopped

## Step 1 — Prove the build on staging, then record it

`make staging-smoke` (7 of 7), then the pull request that records the pass (#222). Repeat the Lab 54 drill so the build
production will boot is the one tested over a database one change behind.

## Step 2 — Promote, then switch on the reading parts

- `promote.yml` pins production to the passed build (#223).
- A pull request sets `costReader.enabled` and `costReader.wasteScan.enabled` in production's values file **and** the
  hand-kept copy in `deploy/gitops/prod/helmrelease.yaml` (#224). Removal stays off. Nothing happens yet: production is
  parked.

## Step 3 — Apply production's Terraform while parked (plan shown first, on the owner's go)

```bash
terraform -chdir=infra/envs/prod plan -out=<file>      # read it; expect adds for the cost reader, waste scan, removal worker
terraform -chdir=infra/envs/prod apply <file>          # only when told
terraform -chdir=infra/envs/prod plan                  # "No changes"
```

2026-10-21: 26 added, 4 changed (launch template AMI, the group's tags, one server permission, the budget Lambda's tags),
0 destroyed. Servers were still parked afterwards; `make tag-scan` still read 0.

## Step 4 — Switch production on

1. Snapshot the stopped database's data volume (the way back).
2. `bash scripts/ops/resume-database.sh`.
3. Move `node.auto.tfvars` out of the way, plan (desired 0 → 1), show it, apply on the owner's go.
4. Check from the server over Session Manager (never exec into a service): pods, the database migration
   revision, Prometheus targets, memory, the "still alive" alarm.

2026-10-21: the first boot applied the one waiting migration by itself and the permissions pass ran after Ready, exactly as
Lab 54 predicted. 10 pods running, 12 of 12 monitoring targets, memory 54%, alarm OK.

## Step 5 — The first scan, by hand

- Invoke the waste-scan Lambda once (read-only). It found 0 and wrote its answer to the parameter.
- The server's cost-pull timer copies it into a ConfigMap; the reader took it in on its next 5-minute pass
  (`kaval_waste_scan_failing` 0, `kaval_waste_findings` 0).
- The cost reader pulled the bill (`kaval_cost_calls_this_month` 1 of the 60 allowed).
- A dump by hand (`systemctl start kaval-db-backup.service`): 312K landed in the backup bucket.
- `make tag-scan`: 0 missing of 124 resources.

## Step 6 — Only then, switch on removal (its own pull request)

`removal.enabled: true` in both production files, `graceHours: 168` (#225). A test makes the chart's wait, the worker's
default and this lab agree. After the merge: the release upgraded, executor and planner show the switch on, 10 pods
running. The planner says "nothing waiting" because the scan found nothing.

## Not yet seen live (be honest about it)

- The scheduled 19:30 UTC dump and 03:15 UTC scan firing by themselves (run by hand instead; `KAV-90` stays open for them)
- The 6-hour `KavalRemovalOrderUnanswered` alarm, `delete_old_snapshot`, the refusal of another environment's disk
- A first real removal order in production: the production worker has never been used

## Parking again

Runbook step 8: put `node.auto.tfvars` back, plan, show, apply on the owner's go, then
`bash scripts/ops/pause-database.sh`. Not done at the time of writing; production was left up on purpose.
