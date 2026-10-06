# Lab 27 — The restore drill

**Phase:** 4 · **Time:** ~25 min, mostly recovering from two script bugs the drill found
**Cost:** one extra gp3 volume plus two extra snapshots (the pre-stop one and the DLM daily),
a few cents; deleted the old volume manually after this lab (see below)

Lab 26 built `make db-restore-snapshot` and deliberately didn't run it against prod — a
destructive-ish action (stop Postgres, swap the data volume) big enough to need its own
go-ahead. This lab is that go-ahead: an actual, timed restore from a real snapshot against the
real production database. `ROADMAP.md`'s "Restore drill" line asked for exactly this: measured
RTO, and the runbook verified by running it, not just reading it.

---

## Setup

The database was in its normal paused posture (`Phase 4: paused between sessions`) — instance
stopped, snapshots current from the nightly DLM policy. Resumed just the database
(`resume-database.sh`), not the full `make up`, since the drill only needed the DB server:

```
$ bash scripts/ops/resume-database.sh
Starting database instance i-03ab2a6fccaceff7c...
Running -- waiting for SSM...
Database online.
Database health check passed (pg_isready + SELECT 1).
```

Newest snapshot available: `snap-046e3f14b6ff168bc`, from that morning's DLM daily run — good
enough to restore from without forcing a fresh one.

## Running it

```
$ echo y | make db-restore-snapshot SNAPSHOT=snap-046e3f14b6ff168bc
Instance:        i-03ab2a6fccaceff7c (ap-south-1a)
Current volume:  vol-04ded6d8d8e9f8373  (will be detached, tagged Reason=damaged, kept)
Restoring from:  snap-046e3f14b6ff168bc
Stopping Postgres and unmounting /data...
Detaching vol-04ded6d8d8e9f8373...
  Detached and tagged. Not deleted -- verify the restore before removing it by hand.
Creating a new volume from snap-046e3f14b6ff168bc...
  vol-06b98fb00709dd812 created.
Attaching vol-06b98fb00709dd812 to i-03ab2a6fccaceff7c...

An error occurred (InvalidParameterValue) when calling the AttachVolume operation: Value
(C:/Program Files/Git/dev/sdf) for parameter device is invalid.
```

The script died here. Not a logic bug — this run happened from Git Bash on Windows, which
rewrites a bare `/dev/...` command-line argument into a Windows path before the AWS CLI ever
sees it (MSYS path conversion). `--device /dev/sdf` became `--device "C:/Program Files/Git/dev/sdf"`.
Nothing live was damaged: the old volume was already safely detached and tagged, the new one
was created but not yet attached, Postgres was stopped. A real but recoverable mid-drill state —
exactly the kind of thing a drill is supposed to surface.

Completed the remaining steps by hand with the fix applied (`MSYS_NO_PATHCONV=1`):

```
$ export MSYS_NO_PATHCONV=1
$ aws ec2 attach-volume --volume-id vol-06b98fb00709dd812 --instance-id i-03ab2a6fccaceff7c --device /dev/sdf
{ "State": "attaching", ... }
```

The mount+restart step hit a second, unrelated bug: the AWS CLI's `--parameters` shorthand
parser chokes on a double quote nested inside a `commands=[...]` string (the original script
wrapped `$DEV` in quotes for the `[ -e "$DEV" ]` test). Worked around it live with a JSON
parameters file; fixed properly afterward by just not quoting `$DEV` — it's a device path with
no spaces, so it never needed the quotes.

```
$ aws ssm send-command --parameters file://mount-params.json ...
{ "Status": "Success" }
```

Reconciled Terraform state exactly as Lab 26 documented (`state rm` + `import` on the volume and
its attachment) — went cleanly both times:

```
$ terraform -chdir=infra/envs/prod import module.database.aws_ebs_volume.data vol-06b98fb00709dd812
Import successful!
$ terraform -chdir=infra/envs/prod import module.database.aws_volume_attachment.data /dev/sdf:vol-06b98fb00709dd812:i-03ab2a6fccaceff7c
Import successful!
```

Final verification:

```
$ make db-health-check
Database health check passed (pg_isready + SELECT 1).
```

## RTO

| From | To | Elapsed |
|---|---|---|
| "yes, go ahead" confirmation | verified-healthy on the restored volume | **~3 min 51 s** |

That number includes recovering from the two CLI bugs above by hand. A clean run (with the
script fixes in this lab) would be faster — the AWS-side steps (detach, create-volume,
attach-volume, SSM round trips) are the actual floor, somewhere around 1.5–2 minutes based on
the individual `wait` calls observed during the drill.

## Data check

```sql
SELECT (SELECT count(*) FROM signal) AS signals, (SELECT count(*) FROM incident) AS incidents;
--  0 | 0
```

Both zero, same before and after. Not a restore failure — prod's event pipeline (`correlate`,
the collector) has been proven on local/dev and k3d, not run continuously against the real prod
cluster yet, so there was no real incident data to lose in the first place. The drill verified
the *mechanism* (stop → detach → restore → reattach → remount → restart → reconcile state), not
data recovery, because there was no production data yet to recover. Worth re-running this drill
once prod actually has incident history, to verify data survives a real restore, not just that
the plumbing does.

## Fixed in this lab

`scripts/ops/restore-snapshot.sh`:

- `export MSYS_NO_PATHCONV=1` added near the top — Git Bash-specific, harmless everywhere else —
  so `--device /dev/sdf` reaches the AWS CLI unmangled.
- The mount+restart SSM command no longer quotes `$DEV`, avoiding the shorthand-parser's nested
  double-quote failure.

Both bugs were latent since Lab 26 — the script was syntax-checked and reviewed there, never
actually run. This is the whole reason a drill, not a read-through, was the right next step.

## Cleanup

The old volume (`vol-04ded6d8d8e9f8373`, detached, tagged `Reason=damaged`) was reviewed and
deleted by hand after confirming the restored volume was healthy and Terraform-tracked —
`prevent_destroy` on the module's resource never applied to it once it was no longer the
resource Terraform manages, so deleting it was a plain `aws ec2 delete-volume`, not a Terraform
change.

A second Terraform drift, unrelated to the restore: the newly-imported volume didn't carry the
`Env`/`ManagedBy` tags the module normally sets (only `Name`/`Project`/`Role`, set directly by
the script). Fixed with a targeted apply:

```
$ terraform -chdir=infra/envs/prod apply -auto-approve \
    -target=module.database.aws_ebs_volume.data \
    -target=module.database.aws_volume_attachment.data
```

`terraform plan` afterward showed only the pre-existing, unrelated app-node ASG drift (desired
capacity 0→1 — expected, since the drill deliberately never brought the app node up).

The database was paused again afterward (`pause-database.sh` — pre-stop snapshot, then stop),
back to Phase 4's standing posture.

## What this doesn't do

- Doesn't prove data survives a restore under real write load — there was no production data to
  lose yet (see "Data check" above).
- Doesn't test the systemd boot-time health-check half (Lab 26) — that only fires on an actual
  instance replacement, which this drill didn't cause.
- Doesn't rehearse this against staging, since `infra/envs/staging` doesn't exist yet — this was
  necessarily a prod-only drill.

## Related

- [Lab 26](lab-26-startup-health-check.md) — built the scripts this lab exercised
- [Restore runbook](../runbooks/restore-from-backup.md) — the procedure now verified live
- [ADR-0008](../adr/0008-production-database-on-its-own-server.md) — the ask-before-restore design
- `ROADMAP.md` Phase 4 — "Restore drill" line, now ticked
