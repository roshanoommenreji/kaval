# Lab 24 — Pre-stop snapshot, and `make down` actually pausing the database

**Phase:** 4 · **Time:** ~30 min build, plus one `terraform plan`/`apply` cycle
**Cost:** $0 — EBS snapshots bill only changed blocks, already priced into ADR-0008

ADR-0008's own stopping table always said the app node and the database pause **together** on
`make down` / `make up`. That was never actually true: `make down` only ran
`terraform destroy -target=module.node`, so the database kept running, billing its full
~$14.40/mo, every time the app node was paused between sessions. This lab closes that gap and
builds the pre-stop snapshot ADR-0008 also describes — one extra EBS snapshot taken right before
any stop, fresher than the daily DLM snapshot could be. See
[ADR-0008's 2026-10-05 amendment](../adr/0008-production-database-on-its-own-server.md) for the
full reasoning.

Like Lab 23, this is bugs found live, not guessed — a `terraform plan` run for this story
surfaced a real, pre-existing problem nobody had looked at since KAV-32's first apply.

---

## What's built

### 1. `scripts/ops/pause-database.sh` / `resume-database.sh`

Called from `make down` / `make up`. Idempotent either way — safe to run against an
already-stopped or already-running database. `pause-database.sh`:

1. Finds the database instance by its `Role=database` tag.
2. If it's already stopped, exits immediately.
3. Finds its data volume by `Role=database-data`, snapshots it
   (`Reason=pre-stop`, `Project=kaval`, `Role=database-data` — the exact tags
   `docs/runbooks/restore-from-backup.md`'s existing recovery query already filters on).
4. Stops the instance (`aws ec2 stop-instances`, never terminate) and waits for `stopped`.

`resume-database.sh` is the inverse: starts the instance if it's not already running, waits for
`instance-running`, then polls SSM until the instance reports `Online`. It does **not** run a
Postgres-level health check (`pg_isready` + sanity query) — that's still the separate, deferred
`ROADMAP.md` line ("Start-up health check"); this script only gets the instance itself back.

### 2. The hard-stop Lambda snapshots before it stops

`infra/modules/budget/lambda/hard_stop.py`'s `_stop_tagged_instances()` already stopped the
database at the `$38` threshold (it's a `Project=kaval` instance outside an ASG — KAV-30 covered
this in general before the database even existed). It just never snapshotted first. Now, before
calling `stop_instances`, it looks up each instance's `Role=database-data` volume (if it has one
— the dev server doesn't) and snapshots it the same way, tagged the same way. A snapshot failure
is logged and swallowed, never blocks the stop — the Lambda's one job at that threshold is
stopping the bill, not protecting data perfectly.

New IAM, on the Lambda's already-narrow role (`infra/modules/budget/main.tf`):

```hcl
statement {
  sid       = "FindVolumes"
  actions   = ["ec2:DescribeVolumes"]
  resources = ["*"]  # no resource-level scoping exists for this action, same as DescribeInstances
}
statement {
  sid       = "SnapshotProjectVolumes"
  actions   = ["ec2:CreateSnapshot"]
  resources = ["arn:aws:ec2:*:*:volume/*"]
  condition { test = "StringEquals", variable = "aws:ResourceTag/Project", values = ["kaval"] }
}
statement {
  sid       = "TagNewSnapshots"
  actions   = ["ec2:CreateTags"]
  resources = ["arn:aws:ec2:*:*:snapshot/*"]
  # The snapshot doesn't exist yet when CreateSnapshot is evaluated, so it can't be scoped by a
  # resource tag. Scoped instead to "a snapshot this same call just created."
  condition { test = "StringEquals", variable = "ec2:CreateAction", values = ["CreateSnapshot"] }
}
```

### 3. `make down` / `make up`

```makefile
down: ## Destroy the node, pause the database (snapshot first), keep EBS/ECR/S3 state
	cd $(TF_PROD) && terraform destroy -target=module.node
	@bash scripts/ops/pause-database.sh

up: ## Resume the database, provision the spot node and reconcile from Git
	@bash scripts/ops/resume-database.sh
	cd $(TF_PROD) && terraform apply
```

---

## The bug found live: the DLM daily snapshot had never actually run

A `terraform plan` run to review this story's own changes surfaced something unrelated to it:

```
# module.database.aws_dlm_lifecycle_policy.database will be updated in-place
~ resource "aws_dlm_lifecycle_policy" "database" {
    ~ state = "ERROR" -> "ENABLED"
```

The DLM policy had been in AWS's `ERROR` state since the very first `terraform apply` that
created it. Checking why:

```bash
aws dlm get-lifecycle-policy --policy-id <id> --query "Policy.StatusMessage" --output text
# Duplicate tag key 'Name' specified.
```

The cause, in `infra/modules/database/main.tf`'s `aws_dlm_lifecycle_policy`: `copy_tags = true`
already copies every tag from the source volume onto each new snapshot — including its `Name`
tag (`kaval-database-data`). The schedule block *also* set `tags_to_add = { Name =
"kaval-database-data-dlm" }`. DLM evaluates both and rejects the resulting duplicate key outright
— and, unlike a Terraform apply failure, it doesn't surface this loudly; it just sits in `ERROR`
silently, forever, until someone looks. **No daily snapshot had been taken in the month this
database has existed.** The pre-stop snapshot this story adds was, until this was found, the
*only* EBS-level backup this database actually had.

Fixed by dropping `tags_to_add` — `copy_tags` alone already gives every daily snapshot
`Name`/`Role`/`Project` from the source volume, which is everything `tags_to_add` was trying to
add a second time:

```hcl
# No tags_to_add: copy_tags already carries Name/Role/Project from the source volume.
copy_tags = true
```

`terraform plan` after the fix showed exactly `ERROR → ENABLED` and the `tags_to_add` removal as
the only change to that resource — confirmed live:

```bash
aws dlm get-lifecycle-policies --query "Policies[].State" --output text
# ENABLED
```

---

## Reproducing from zero

1. `terraform fmt -check -recursive -diff infra` and `terraform validate` in `infra/envs/prod`
   — both clean, offline, no AWS calls.
2. `terraform plan` in `infra/envs/prod`. Expect exactly: 2 new IAM statements plus a Lambda code
   hash change on `module.budget`, and `ERROR → ENABLED` plus a `tags_to_add` removal on
   `module.database.aws_dlm_lifecycle_policy.database`. Any ASG diff you see is unrelated drift
   from however the app node was last paused, not from this story.
3. Apply. Confirm: `aws dlm get-lifecycle-policies --query "Policies[].State"` returns `ENABLED`.
4. `bash scripts/ops/resume-database.sh` against an already-running database — confirms the
   instance lookup and idempotency check both work, with zero side effects
   (`Database (<id>) is already running.`).
5. The real test — do this the next time you'd pause the system anyway, so it doesn't fight an
   app node that's mid-resume: `make down`, then
   `aws ec2 describe-snapshots --filters Name=tag:Reason,Values=pre-stop --query "reverse(sort_by(Snapshots,&StartTime))[:1]"`
   to confirm a fresh snapshot landed, and `aws ec2 describe-instances --instance-ids <db-id>
   --query Reservations[0].Instances[0].State.Name` returns `stopped`, not `terminated`. Then
   `make up` and confirm `running` again, with the same data (`\dt` on the real tables, not an
   empty schema).

## Known gap, named not hidden

- `resume-database.sh` does not run a Postgres-level health check, and does not restore a
  snapshot automatically on failure — that's `ROADMAP.md`'s separate, still-unbuilt "Start-up
  health check" line. If the volume comes back damaged, today that's still a manual
  `docs/runbooks/restore-from-backup.md` exercise.
- The nightly auto-stop schedule (the third stop path ADR-0008 names) doesn't exist yet. The
  snapshot step was written as its own reusable script precisely so wiring it in later is one
  call, not a rewrite.
- This story was never rehearsed in staging, because `infra/envs/staging` still doesn't exist —
  the same named gap every KAV-32-adjacent story has carried since Lab 22.
