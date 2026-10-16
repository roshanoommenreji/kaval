# Terraform says the state is locked, is wrong, or the laptop is gone

Terraform's record of what exists (prod, staging, dev) lives in one private S3 bucket, not on the laptop
([ADR-0058](../adr/0058-terraforms-record-lives-in-a-private-versioned-bucket.md),
[Lab 55](../labs/lab-55-terraform-state-off-the-laptop.md)). Nothing running reads it: servers keep working when the
bucket is unreachable. A person is the only audience; no fix is proposed automatically.

## Signals

- `Error acquiring the state lock` with a `Lock Info` block (ID, Operation, Who, Created)
- A plan wants to create something that already exists, or destroy something that should stay (the record is wrong)
- `Backend initialization required`, or `terraform plan` on a new laptop says the bucket is missing
- The laptop is lost or reinstalled

## Likely causes

1. Another run is still going (a second terminal, an unfinished `make staging-up`): the lock is correct
2. A run was killed (closed terminal, power loss) and left its lock object behind
3. Someone ran `state rm`, `state mv`, `import` or an apply that failed half way, and the record no longer matches
4. A fresh checkout that has not been connected yet (`tf-init.sh` not run)

## Diagnosis

```bash
export MSYS_NO_PATHCONV=1 AWS_PROFILE=kaval
BUCKET=$(aws ssm get-parameter --name /kaval/tfstate/bucket --query Parameter.Value --output text)
aws s3api list-object-versions --bucket "$BUCKET" --prefix <env>/terraform.tfstate \
  --query 'Versions[?Key==`<env>/terraform.tfstate`].[LastModified,Size,IsLatest,VersionId]' --output text
```

Newest first. A lock is the object `<env>/terraform.tfstate.tflock`; if it exists now, someone holds the lock. Read
`Created` in the error: a lock a minute old belongs to a running command; one hours old with no terminal open is stale.

## Remediation

**A new laptop (reversible, read-only until you apply):**

1. Install Terraform 1.11 or newer, log in to AWS with the `kaval` profile, clone the repository.
2. `bash scripts/ops/tf-init.sh <env>` (finds the bucket itself; `KAVAL_TFSTATE_BUCKET=<name>` if Parameter Store cannot be read).
3. Recreate the gitignored settings files from their `.example` neighbours: `terraform.tfvars` holds the alert email
   (`.env`'s `BUDGET_ALERT_EMAIL`), the budget numbers ([budget-plan.md](../cost/budget-plan.md)) and your **public** SSH
   key (regenerate with `ssh-keygen -y -f <private key>`); prod's `node.auto.tfvars` exists only while it is parked
   (`app_node_desired_capacity = 0`). Without them `terraform plan` asks for the values.
4. `terraform plan`. If it is the same as before the loss, the record is intact.

**A stale lock (reversible; you may break a live run if you are wrong):** first check no command is running (any
terminal, any machine) and that the lock's `Created` time is not recent. Then
`terraform -chdir=infra/envs/<env> force-unlock <ID from the error>`. If that fails, deleting the object
`<env>/terraform.tfstate.tflock` does the same thing.

**The record is wrong (reversible, because every version is kept):**

1. Find the last good version in the listing above (the one before the bad change; compare times and sizes).
2. Put it back as the newest:
   `aws s3api copy-object --bucket "$BUCKET" --key <env>/terraform.tfstate --copy-source "$BUCKET/<env>/terraform.tfstate?versionId=<id>" --server-side-encryption AES256`
3. `terraform -chdir=infra/envs/<env> plan`. Tried on staging 2026-10-16: damaged plan "1 to add", after the copy "No changes".

Old versions are kept at least 90 days (the newest 20 always).

## Do not

- Do not `force-unlock` without checking that no run is live: two writers can damage the record
- Do not edit the state object by hand in the bucket; copy an earlier version instead
- Do not run `terraform destroy` in `infra/envs/state`: the bucket refuses (`prevent_destroy`) and for good reason. The
  record of everything built is in it
- Do not paste a state file anywhere: it contains generated database passwords and the Slack tokens
