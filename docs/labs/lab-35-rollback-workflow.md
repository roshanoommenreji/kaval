# Lab 35 — `rollback.yml`: putting prod back, and timing it

**Phase:** 4 · **Story:** `KAV-66` · **Cost:** about $0.08 (staging up for 1 h 45 min at ~$0.045/hr, then
destroyed). The workflow itself is free. Prod stays parked; nothing was applied to it.

[Lab 34](lab-34-promote-workflow.md) built the gate that only lets prod move forward. This lab builds the way
back, rehearses it on a real cluster, and writes down how long it takes. The decisions are in
[ADR-0034](../adr/0034-rollback-workflow-and-what-a-rollback-may-go-back-to.md); the step-by-step for a real
emergency is [the runbook](../runbooks/rollback-prod.md).

**Result:** `rollback.yml` for `sha-ffb436b` is refused because it crosses a database migration, passes when
told that was dealt with, and opens a pull request that passes all 11 checks. On staging, a rollback across a
migration **stalled** until the migration was undone; after that, merge to healthy took about **80 seconds**.

---

## 1. What exists

| Piece | Where | Job |
|---|---|---|
| The brain | `scripts/release/rollback.py` (+ `test_rollback.py`, 28 tests) | `verify`, `pin`, `body` |
| The workflow | `.github/workflows/rollback.yml` | `gate` (read-only AWS) then `propose` (opens the pull request) |
| The guard change | `prod_history_tags` and a `history` argument in `scripts/release/promote.py` | `promotion-guard` also allows a tag prod has pinned before |
| The runbook | `docs/runbooks/rollback-prod.md` | what a person does, including the database step |

## 2. Run it

```bash
gh workflow run rollback.yml --ref main -f tag=sha-ffb436b -f reason="why, in a sentence"
# add  -f accept_migrations=true  only after the database step in the runbook
```

The `gate` job prints:

```
rollback sha-2459417 -> sha-ffb436b
  PASS  tag is one prod has run before or one that passed staging: prod has run sha-ffb436b before
  PASS  tag is an earlier version of what prod runs: sha-ffb436b is an ancestor of prod's sha-2459417
  PASS  the four images exist in ECR: all four present (older than the record, so no digest to compare)
  FAIL  going back crosses no database migration: crosses 1 migration(s): a1c4f9b0e3d2_action_slack_notified_at.py. ...
prod may NOT be rolled back to sha-ffb436b.
```

With `accept_migrations=true` the last line passes, `propose` opens
`fix(release): roll back prod to sha-ffb436b`, and the log ends with
`Rollback pull request opened 30s after the workflow was started.` The pull request (#86 in the rehearsal)
passed all 11 checks, including `promotion-guard`. It was closed unmerged: prod stays on `sha-2459417`.

A tag prod never ran and that never passed staging (`sha-deadbee`) is refused at the first question.
Prod's own current tag is refused as "nothing to roll back". Run locally without an AWS login the gate prints
`cannot decide` and exits 2.

## 3. The staging rehearsal (the cluster half of time-to-restore)

Prod is parked, so the cluster half was timed on staging, which runs the same Flux and the same chart.

1. `ASSUME_YES=1 bash scripts/ops/staging.sh up` (3 min 55 s). Staging runs `sha-2459417`, so its database has
   migration `a1c4f9b0e3d2`.
2. Staging's Flux follows `main`, so a scratch branch stood in for it: a commit that makes
   `deploy/gitops/staging/source.yaml` follow the scratch branch (both the cluster and the branch must agree,
   or Flux's kustomization puts the setting back), then a second commit that pins the four images to
   `sha-ffb436b`. Pushing the second commit is the stand-in for pressing Merge.
3. A small stopwatch script on the node (over Session Manager) logged when Flux saw the commit and when the pods
   were ready. Cluster timestamps (`lastTransitionTime`) were the source of truth.

### Run 1: across the migration — it stalls

```
Can't locate revision identified by 'a1c4f9b0e3d2'
Helm upgrade failed ... pre-upgrade hooks failed: Job/kaval-staging-migrate-9 status: 'Failed'
Remediated=True RollbackSucceeded: Helm rollback to previous release ... succeeded
```

Every upgrade first runs `alembic upgrade head` from the gateway image. The `sha-ffb436b` image has no
`a1c4f9b0e3d2`, and the database already does. The job failed four times, Flux restored the newer release, and
the services never stopped (the old pods were never touched). It simply did not roll back. Flux noticed the
commit 53 s after the push.

### Run 2: after undoing the migration — it works

A one-off job with the **newer** image ran `alembic downgrade 8f3b1c6a2d94` on staging's database (it drops
one empty column). A new commit then re-triggered Flux (a stalled release only retries on a new revision):

| Event (seconds after the push) | |
|---|---|
| Flux saw the commit | +55 |
| New pods created | +64 |
| Last service Ready | +76 |
| `HelmRelease` `Released=True` | **+80** |

Then `staging.sh down`: `Left tagged Env=staging: 0 instances, 0 volumes, 0 VPCs`.

**Time-to-restore:** button to rollback pull request, 30 s; checks, under 2 min; merge to healthy, about 80 s
(two-thirds of it Flux's once-a-minute check). Roughly 4 to 5 minutes without waiting for a person.

## 4. What went wrong, in order

1. **I assumed a rollback across a migration would run old code on a newer schema.** It does not get that
   far: the migrate step blocks it. That changed what `accept_migrations` means and produced the runbook.
2. **Flux undid my change.** Pointing staging's Flux at a scratch branch with `kubectl patch` was reverted
   within a minute, because the staging kustomization re-applies `source.yaml` from Git. The branch had to say
   the same thing.
3. **My stopwatch never rang in run 2.** It treated the failed pods left from run 1 as "not ready". The
   cluster's own timestamps gave the answer; the lesson is to time from the system's records, not from a
   poller that can disagree with its own earlier state.
4. **A permission check stopped me twice** (merging a pull request, then editing staging's database). Both
   were things you had not approved; I asked, you said yes, and I carried on.

## What this does not do

- It does not undo database changes. That is the person's step, with a snapshot, in the runbook.
- It does not measure prod. Prod's node may pull images it has not got, and its database has real data.
- It cannot compare digests for a tag older than the record; ECR's immutable tags are the guarantee.
- A bot pull request's first CI run still needs a person to approve it.

## Reproduce

1. `gh workflow run rollback.yml --ref main -f tag=sha-ffb436b -f reason=rehearsal`; expect the FAIL on the
   migration. Run again with `-f accept_migrations=true`; expect a pull request. Close it unmerged.
2. Run with a tag that never ran (`sha-deadbee`); expect the first check to FAIL.
3. For the cluster timing, follow section 3 on staging and destroy it afterwards.
