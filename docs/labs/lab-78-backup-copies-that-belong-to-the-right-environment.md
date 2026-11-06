# Lab 78 — Backup copies that belong to the right environment

**Phase:** 8 (hardening) · **Stories:** `KAV-127`, `KAV-128` · **Time:** about 40 minutes · **Cost:** $0 for Parts 1 to 3; the staging proof (Part 4) shares one staging session (about 30 minutes at $0.045 an hour, roughly 2 cents) plus one 10 GB copy that lives for a few minutes

Two small flaws, one story each, both about the backup copies (EBS snapshots) of the database disk. `make staging-down` left some behind because it looked for them by *name*, and the daily schedule copied the *other* environment's disk because AWS matches *any one* of its tags. This lab shows both without changing anything, makes them stop, and proves the fix. The decision is [ADR-0078](../adr/0078-backup-copies-are-found-by-their-labels-not-their-names.md).

## Prerequisites

- The AWS login the project uses (`AWS_PROFILE=kaval`) for the read-only Parts, `bash`, and the project's Python environment (`make sync`).
- Parts 1 to 3 change nothing in AWS. Part 4 needs a staging session and the owner's go.

## Part 1 — See both flaws, read-only

**The schedule (`KAV-128`).** List the account's backup schedules and what each one targets:

```bash
aws dlm get-lifecycle-policies --query "Policies[].[PolicyId,State,Description]" --output text
aws dlm get-lifecycle-policy --policy-id <the id> --query "Policy.PolicyDetails.TargetTags" --output text
```

Before the fix, production's schedule (the only one while staging is gone) printed two lines:

```
Role    database-data
Name    kaval-prod-database-data
```

AWS covers a disk that has **any one** of those, not both ([How Amazon Data Lifecycle Manager works](https://docs.aws.amazon.com/ebs/latest/userguide/dlm-elements.html)). Every environment's disk is labelled `Role = database-data`, so this schedule matched staging's disk as well, and staging's own schedule (same module) matched production's. That is what happened on 2026-11-04 at 03:15 UTC.

**The labels (`KAV-127`).** Count the account's copies by label (`--output text` prints a bare number):

```bash
q() { aws ec2 describe-snapshots --owner-ids self "$@" --output text; }
q --query 'length(Snapshots)'
q --filters Name=tag:Project,Values=kaval Name=tag:Env,Values=staging --query 'length(Snapshots)'
q --filters Name=tag:Project,Values=kaval Name=tag:Env,Values=prod --query 'length(Snapshots)'
q --query "length(Snapshots[?!(Tags[?Key=='Env'])])"      # copies with no Env label at all
```

On 2026-11-06 with staging gone: 20, 0, 20, 0. The middle number is what the new teardown would delete; the third is a control that the search sees production's copies; the last says every copy carries the label.

Why labels and not names: the nightly stop's Lambda names its copy `kaval-hard-stop-pre-stop` for **every** environment (`infra/modules/budget/lambda/hard_stop.py`), and `staging.sh down` looked for `kaval-staging-database*`. A staging that was still up at 02:00 got a copy that the teardown never found. Three were left behind (22 Oct, 27 Oct, 4 Nov) until they were deleted by hand.

## Part 2 — The fixes

`scripts/ops/staging.sh` finds the copies by their labels, refuses a suspicious number, stops if it cannot list them, and counts what is left:

```bash
STAGING_COPY_FILTERS=("Name=tag:Project,Values=kaval" "Name=tag:Env,Values=staging")
MAX_COPIES_AT_ONCE=30
```

`infra/modules/database/main.tf` targets the `Name` tag alone, which only one environment's disk carries:

```hcl
target_tags = {
  Name = "${var.name_prefix}-database-data"
}
```

## Part 3 — The tests

```bash
uv run pytest scripts/dev/test_staging_snapshots.py scripts/dev/test_snapshot_schedule_targets.py -v
```

Fourteen pass (eight and six).

| Test | What it proves |
|---|---|
| every copy labelled as staging's is deleted, including the nightly stop's | The bug: that copy has staging's labels and not staging's name |
| nothing that is not staging's is touched | Production's copies, the one staging's schedule once made of production's disk, a copy of unknown environment and someone else's all stay |
| a second teardown finds nothing and still succeeds | Safe to run again after a half-finished teardown |
| no staging copies, nothing deleted | The negative case |
| refuses a suspicious number of copies | 31 staging-labelled copies: it says `refusing`, exits non-zero, deletes none |
| a failing listing stops the teardown | An expired login used to read as "no copies" |
| the closing line counts the copies left | `Left tagged Env=staging: 0 instances, 0 volumes, 0 VPCs, 0 snapshots` |
| each schedule covers its own disk | The control: without it a schedule that matched nothing would pass the next test, and nothing would be backed up |
| no schedule covers another environment's disk | AWS's rule applied both ways to the real Terraform of every environment |
| the check does see the old mistake | The old `Role` plus `Name` pair is caught by the same rule |

The teardown tests run the real `staging.sh down` against a pretend `aws` that holds both environments' copies and applies filters the way AWS does. It sits first on the PATH, and the AWS settings point at nothing, so a mistake in the test fails rather than deletes.

**Seen failing first.** Put the old files back and run the same command:

```bash
git checkout 88a8518 -- scripts/ops/staging.sh infra/modules/database/main.tf
uv run pytest scripts/dev/test_staging_snapshots.py scripts/dev/test_snapshot_schedule_targets.py
git checkout HEAD -- scripts/ops/staging.sh infra/modules/database/main.tf
```

The old teardown printed `Deleting snapshot snap-0a2`, `snap-0a3` and `snap-0a4` and never `snap-0a1` (the nightly stop's), and ended `Left tagged Env=staging: 0 instances, 0 volumes, 0 VPCs` with no count. Both environments' schedules were reported as covering the other's disk "because [its] disk carries `{'Role': 'database-data'}`".

**Seen failing the other way.** Taking `Name=tag:Env,Values=staging` out of `STAGING_COPY_FILTERS` made four tests fail (production's copies would have been deleted). The file was put back and checked identical.

## Part 4 — The proof on staging (after the merge; every step needs the owner's go)

Staging must be gone before you start (`Left tagged Env=staging: 0 ...`).

**4a — The real thing, across a night (recommended; about 15 cents).** A copy made by hand only shows that the teardown can find one. A night shows the real thing: the nightly stop's own copy (02:00 IST) and the schedule's own run (03:00 UTC, which is 08:30 IST). Normally a staging session ends before 02:00 IST, because the nightly stop ends the cluster; here that is the point.

1. In the evening, `make staging-up` (about 7 minutes; the meter runs). Then leave it alone.
2. **The schedule it made.** `aws dlm get-lifecycle-policies` now lists two. For the one whose description starts `kaval-staging`, `aws dlm get-lifecycle-policy --policy-id <id> --query "Policy.PolicyDetails.TargetTags" --output text` must print one line, `Name   kaval-staging-database-data`. Production's still prints both lines until it is applied.
3. **Count by label** as in Part 1: staging 0, production 20. Note which of production's copies carry which schedule: `q --filters Name=tag:Env,Values=prod --query "Snapshots[].Tags[?Key=='aws:dlm:lifecycle-policy-id'].Value | []"` (every copy a schedule makes carries that tag; on 2026-11-06 production had eight made by its own schedule and one made by a different one, the stray copy of 4 Nov).
4. Overnight there is nothing to do. At 02:00 IST the nightly stop copies staging's data disk under the name `kaval-hard-stop-pre-stop` and stops the node and the database server; at 03:00 UTC the schedules run (a schedule snapshots a disk whether or not its server is running).
5. **Morning, after 08:45 IST, read-only.** Staging now has copies: the nightly stop's and the schedule's, plus one more from production's schedule while that is still unfixed (it covers any disk with `Role = database-data`), so expect three. Production's own disk must have gained **exactly one** copy since midnight UTC, made by production's own schedule and none by staging's (before the fix it would have been two): list the copies of production's data disk (`--filters Name=volume-id,Values=<its volume id>`) and read each one's `aws:dlm:lifecycle-policy-id`.
6. `make staging-down`. It must print `Deleting snapshot ...` for each of staging's copies, the nightly stop's among them, and end `Left tagged Env=staging: 0 instances, 0 volumes, 0 VPCs, 0 snapshots`. The old script would have left the nightly stop's.
7. Count again: staging 0, production exactly as in step 5.

**4b — The quick version, with a copy made by hand (about 30 minutes, 2 cents).** It proves the teardown and the schedule's targets, not the schedule's own run.

1. `make staging-up`, then step 2 above.
2. **Plant a copy that looks like the nightly stop's**, on staging's data disk, with the same labels the Lambda sets (this is an AWS write, a 10 GB copy for a few minutes):

   ```bash
   VOL=$(aws ec2 describe-volumes --filters "Name=tag:Name,Values=kaval-staging-database-data" \
     "Name=tag:Env,Values=staging" --query "Volumes[0].VolumeId" --output text)
   SNAP=$(aws ec2 create-snapshot --volume-id "$VOL" \
     --description "KAV-127 proof: planted by hand, labelled like the nightly stop's copy" \
     --tag-specifications 'ResourceType=snapshot,Tags=[{Key=Name,Value=kaval-hard-stop-pre-stop},{Key=Reason,Value=pre-stop},{Key=Project,Value=kaval},{Key=Env,Value=staging},{Key=ManagedBy,Value=kaval-lambda},{Key=Role,Value=database-data}]' \
     --query SnapshotId --output text)
   aws ec2 wait snapshot-completed --snapshot-ids "$SNAP"
   ```

3. Count by label as in Part 1: staging 1, production still 20.
4. `make staging-down`. It must print `Deleting snapshot <the planted one>` and end `Left tagged Env=staging: 0 instances, 0 volumes, 0 VPCs, 0 snapshots`. The old script would have left it.
5. Count again: staging 0, production still 20 (and the same twenty ids).

**Result:** not run yet. This section is filled in at the proof.

## Part 5 — Production, at its next bring-up

Nothing to deploy by itself: the schedule change is in production's Terraform and rides with the changes already waiting for the bring-up. On 2026-11-06 production's plan read `2 to add, 3 to change, 0 to destroy` without this change and `2 to add, 4 to change, 0 to destroy` with it; the extra line is `module.database.aws_dlm_lifecycle_policy.database`, whose `target_tags` loses `Role`. After the apply, `get-lifecycle-policy` must print only `Name   kaval-prod-database-data`, and the next 03:00 UTC (08:30 IST) run must add one copy of production's own disk.

## Done when

- [x] Both flaws seen read-only, and the real account measured: 20 copies, 0 staging-labelled, 20 production-labelled, 0 without a label *(2026-11-06)*
- [x] Tests written and seen failing first, and failing the other way *(2026-11-06)*
- [x] `staging-down` finds copies by label, refuses more than 30, stops on a failed listing, counts what is left *(2026-11-06)*
- [x] The schedule targets the `Name` tag alone, with a test that applies AWS's rule both ways *(2026-11-06)*
- [x] Production's plan with the change read: one extra in-place change, nothing applied *(2026-11-06)*
- [ ] Merged with CI green
- [ ] Proved on staging: the schedule it builds targets staging's disk only, production's disk gains no copy from it at 03:00 UTC, and a real teardown after a night's stop deletes the nightly stop's copy and nothing of production's *(Part 4a; or 4b for the teardown alone)*
- [ ] Production's schedule changed, at its next bring-up *(Part 5)*

## Cost

$0 to build and for Parts 1 to 3. Part 4: one staging session (about 30 minutes at $0.045 an hour) and a 10 GB copy for a few minutes. The fix saves up to about $1.50 a month that a forgotten nightly-stop copy costs (30 GB at $0.05 per GB-month in Mumbai, an upper bound because copies are incremental), and the stray copies of the other environment's disk (cents).
