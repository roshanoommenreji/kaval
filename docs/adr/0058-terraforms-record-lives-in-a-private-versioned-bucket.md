# ADR-0058 — Terraform's record of what exists lives in a private, versioned bucket

- **Status:** Accepted; built and proven 2026-10-16 (see "Measured")
- **Date:** 2026-10-16
- **Deciders:** Roshan
- **Story:** `KAV-86` (Phase 5) · closes the first of the two gaps written down in [disaster-recovery.md](../architecture/disaster-recovery.md) ([ADR-0049](0049-backups-watched-and-restores-drilled.md)) · follows [ADR-0004](0004-environment-strategy-and-promotion.md) (environments differ only by values) and [ADR-0008](0008-production-database-on-its-own-server.md)

## Context

Terraform keeps a "state" file: its record of every resource it built. Until today the records for prod, staging and
dev were plain files on one laptop (`infra/envs/*/terraform.tfstate`, never in git). Three consequences:

1. **Losing the laptop loses the record, not the infrastructure.** Production would keep running, but rebuilding
   Terraform's view of its 82 resources would mean importing each one by hand.
2. **No history.** A wrong `state rm` or a crashed apply overwrote the only copy. Staging's folder held eleven
   hand-made `.backup` files for that reason.
3. **No lock.** Two runs at once (seen in `KAV-83` part 3: three applies from one laptop) could write over each other.

The record is also **sensitive**: it holds the generated database passwords and the Slack tokens. Whatever replaces
the laptop must be private and encrypted.

## What real companies do

| Pattern | One line | Fits here? |
|---|---|---|
| S3 bucket, versioning, encryption, native lock | Terraform's S3 backend; the lock is a small object next to the state (`use_lockfile`, Terraform 1.10+) | **Yes.** Needs nothing but the bucket |
| S3 bucket plus a DynamoDB lock table | The older form of the same | No: the table is being phased out in Terraform 1.11 and is a second thing to pay for and lose |
| Terraform Cloud / a hosted runner | A vendor stores the state and runs the applies | No: a new vendor and a recurring bill for one person, and applies would move off the laptop, a bigger change than this story |
| Keep local files, copy them somewhere | Backup by hand | No: that is the present state, with extra steps |

**Chosen:** the first.

## Decision

1. **One bucket, one object per environment** (`prod/`, `staging/`, `dev/`, `state/` `terraform.tfstate`), in the
   region of the rest (ap-south-1). One bucket, not one per environment: there is one person and one account, so
   separate buckets would add names to lose without separating anyone's access.
2. **The bucket has its own folder, `infra/envs/state`.** `make staging-down` destroys everything in staging's
   record; the bucket must never be inside anything that is destroyed. It is built with local state first (a bucket
   cannot hold the record of its own creation) and then moves its own record into itself, which is the usual answer.
3. **Protections:** versioning (every earlier record kept; versions older than 90 days expire but the newest 20 are
   always kept), encryption with the free S3 key (a customer key costs $1 a month and is one more thing to lose),
   public access blocked four ways, a policy refusing any request that is not TLS, `prevent_destroy` and no
   `force_destroy` on the bucket.
4. **Locking is S3's own** (`use_lockfile = true`). Every environment requires Terraform 1.11 or newer (CI already
   runs 1.11.4).
5. **The bucket's name stays out of git** (public repository). It is in Parameter Store at `/kaval/tfstate/bucket`
   (free, not a secret), and `scripts/ops/tf-init.sh <env>` reads it and runs `terraform init` with it. The
   environment's key, region, encryption and lock are in its own `backend "s3"` block. On a new laptop the whole
   recovery is: AWS login, `tf-init.sh`, `terraform plan`.
6. **A CI test (`scripts/dev/test_terraform_state.py`) keeps it that way:** every environment must name an S3 backend
   with the lock on, encryption on, its own key and no bucket name; no `.tfstate` file may be tracked; the bucket's
   real name may not appear in any committed file; the bucket keeps its protections. Each rule has a negative test
   (the previous prod configuration fails it).

## Security

- **Who can read it:** the AWS admin login the laptop already uses, and nobody else. No new key exists. A separate
  read-only role for CI to run plans belongs with the Phase 8 plan-in-CI work (a roadmap line and Jira story were
  added with this change); it is not built here because CI has no AWS credentials today, deliberately.
- **What leaks if the bucket is opened:** the whole record, including generated passwords and the Slack tokens. That
  is why public access is blocked four ways, plain HTTP is refused (tried: `AccessDenied`) and nothing is ever made
  public by a policy.
- **What a mistake can do:** a wrong overwrite is undone from an earlier version (runbook). A deleted bucket is
  refused by `prevent_destroy`; the history is inside the bucket, so that guard is the one that matters.
- **Not covered:** the AWS login itself. If it is lost, the bucket is unreachable; that is the same gap as every other
  piece of AWS and is outside this story.

## Failure and rollback

- **A run crashes and leaves a lock:** the next run refuses and prints who holds it and since when. The runbook has
  the one command to clear it, after checking nobody is running.
- **The bucket or the region is unavailable:** Terraform cannot plan or apply until it is back. Nothing running
  stops: servers do not read the record.
- **Going back:** `terraform init -migrate-state -force-copy -backend-config=...` against a local backend copies the
  record down again. The old local files were kept (renamed) for a week after the move.
- **The record is wrong or damaged:** restore an earlier version; tried on staging, runbook
  [terraform-state.md](../runbooks/terraform-state.md).

## Observability

S3 keeps every version with its time, so the history of the record is itself the log. The lock object names the
operation and who holds it. No alert: nothing here runs on its own, so there is nothing to go silent. The CI test is
the guard against the setup quietly being undone.

## Cost and capacity

**About $0.01 a month or less, and it adds nothing to the app server.** Measured 2026-10-16 after moving all four
records and the proofs: 21 object versions, 0.7 MB, which is $0.00002 at S3's standard storage price; requests for a
plan or apply are a handful of cents per thousand runs. No key fee, no lock table. Recorded in
[budget-plan.md](../cost/budget-plan.md).

## Testing

Unit (`scripts/dev/test_terraform_state.py`, 18 tests): the backend rules above with checkers that also fail broken
input (no backend, a commented-out backend, no lock, a wrong key, a bucket name in git). Live: see "Measured".

## People and process

[Lab 55](../labs/lab-55-terraform-state-off-the-laptop.md) builds it from zero. Runbook
[terraform-state.md](../runbooks/terraform-state.md): new laptop, stuck lock, restoring an earlier version. One thing
the record alone does not rebuild: each environment also has a gitignored `terraform.tfvars` (alert email, budget
numbers, SSH public key). The runbook says where each value comes from.

## What this does not do

- It does not run plans or applies anywhere but the laptop (Phase 8).
- It does not protect against loss of the AWS login.
- It does not make the old `terraform.tfvars` files recoverable from the bucket.

## Measured

2026-10-16, on staging (up), dev, prod (parked) and the bucket's own folder:

| Step | Result |
|---|---|
| Records moved | prod 82, staging 62, dev 8, state 10 resource blocks; the copy in the bucket equals the old file for every one (resources and outputs compared) |
| `plan` after the move | state, staging: **No changes**. Prod: 2 in place (the launch template and the group's version, from the `KAV-85`/`KAV-88` start-up script changes merged since prod was last applied); the same two before and after the move. Dev: its server would be replaced (a newer machine image, a public address setting); older than this story, not applied |
| Lock | a second `plan` 6 seconds into the first was refused (`Error acquiring the state lock`, status 412) with the holder's id, operation and start time; the first finished normally |
| Second checkout | a fresh clone, only the gitignored settings files copied in, `tf-init.sh staging`: **No changes**; prod: the same 2 in place as in the first checkout |
| Restore | a resource removed from staging's record on purpose (the plan then wanted to create it: 1 to add); the earlier version copied back as the newest; the plan returned to **No changes**; 76 entries listed again |
| Protections | plain HTTP refused, public access blocked (4 of 4), encryption AES256, versioning on |
