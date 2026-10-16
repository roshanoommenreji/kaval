# Lab 55 — Terraform's record moves off the laptop

**Phase:** 5 · **Story:** `KAV-86` · **Time:** about 1 hour · **Cost:** under $0.01 a month, nothing else.

Until now Terraform's record of what exists (the "state") was a file on one laptop per environment. This lab builds the
bucket that holds it, moves each record in, and proves the three things that matter: it is locked, a second checkout can
use it, and an earlier version can be brought back. The decision is
[ADR-0058](../adr/0058-terraforms-record-lives-in-a-private-versioned-bucket.md).

## Prerequisites

- Terraform 1.11 or newer (the lock needs 1.10), `AWS_PROFILE=kaval`, Git Bash
- On Windows with Git Bash, set `MSYS_NO_PATHCONV=1` before any `aws ssm` command with a leading `/` (the shell
  otherwise rewrites the name; `tf-init.sh` does this itself)
- Staging may be up or parked; prod stays parked. Only records move, no resource is touched

## Step 1 — The rules, with no AWS

```bash
pytest scripts/dev/test_terraform_state.py -q        # 18 tests
```

Read `backend_problems` and its negative tests first: an environment with no backend, a commented-out backend, no lock,
the wrong key or a bucket name written in git is flagged. Run it against the previous prod file
(`git show 802e05c:infra/envs/prod/main.tf`): it says "no s3 backend block".

## Step 2 — Build the bucket

```bash
cd infra/envs/state
terraform init
terraform plan -out=state.tfplan        # 9 to add, 0 to change, 0 to destroy
terraform apply state.tfplan
```

Nine things: a random name suffix, the bucket, versioning, the public-access block, ownership, encryption, the
old-versions rule, the TLS-only policy and the Parameter Store entry (`/kaval/tfstate/bucket`). Check:

```bash
export MSYS_NO_PATHCONV=1
aws ssm get-parameter --name /kaval/tfstate/bucket --query Parameter.Value --output text
aws s3api get-bucket-versioning --bucket <that name>        # Enabled
```

## Step 3 — The bucket moves its own record into itself

The block `backend "s3"` in `infra/envs/state/main.tf` is already switched on in git. Initialise with the new
script, which reads the bucket's name and passes it:

```bash
cp infra/envs/<env>/terraform.tfstate <somewhere safe>        # for each env, before anything
bash scripts/ops/tf-init.sh state -migrate-state -force-copy
terraform -chdir=infra/envs/state plan                         # No changes
```

## Step 4 — Move each environment (staging, dev, prod, in that order)

```bash
bash scripts/ops/tf-init.sh staging -migrate-state -force-copy
terraform -chdir=infra/envs/staging plan                       # No changes
```

Then, for each, prove the copy in the bucket equals the old file:

```bash
aws s3 cp s3://<bucket>/staging/terraform.tfstate remote.json
# compare the "resources" and "outputs" of remote.json and the saved old file: identical
```

Seen on 2026-10-16: prod 82 resource blocks, staging 62, dev 8, the bucket's own folder 10; all four identical.
Prod's plan showed **2 in place** (the launch template and the group's version), the same two as before the move: they
come from the start-up script changes of `KAV-85` and `KAV-88`, not from the move (and the plan is never applied
here). Dev's plan showed its server would be replaced: a newer machine image and a public-address setting, older than
this story; left alone.

Afterwards rename the old file so nobody uses it by mistake (`terraform.tfstate.migrated-<date>`, still gitignored)
and delete it after a week.

## Step 5 — Proof 1: the lock

```bash
terraform -chdir=infra/envs/staging plan &                      # holds the lock for about a minute
sleep 6
terraform -chdir=infra/envs/staging plan -lock-timeout=0s       # refused
```

Seen: `Error acquiring the state lock` (status 412) with `Lock Info` showing the holder's id, the operation
(`OperationTypePlan`), who and when. The first run finished with no errors.

## Step 6 — Proof 2: a second checkout

```bash
git clone --branch <the branch> . ../checkout2
cp infra/envs/staging/terraform.tfvars ../checkout2/infra/envs/staging/     # gitignored settings are not in git
cd ../checkout2 && bash scripts/ops/tf-init.sh staging
terraform -chdir=infra/envs/staging plan                         # No changes
```

Prod the same way (its `terraform.tfvars` and `node.auto.tfvars` copied in) showed the same two in-place changes as the
original checkout. The settings files matter: the record alone is not enough, see the runbook.

## Step 7 — Proof 3: restore an earlier version

On staging. This is the realistic accident, someone removing a resource from the record:

```bash
aws s3api list-object-versions --bucket <bucket> --prefix staging/terraform.tfstate     # note the good VersionId
terraform -chdir=infra/envs/staging state rm 'aws_ssm_parameter.slack["channel-id"]'
terraform -chdir=infra/envs/staging plan          # damage: 1 to add (it wants to create what already exists)
aws s3api copy-object --bucket <bucket> --key staging/terraform.tfstate \
  --copy-source "<bucket>/staging/terraform.tfstate?versionId=<good id>" --server-side-encryption AES256
terraform -chdir=infra/envs/staging plan          # No changes
```

Seen: damaged plan "1 to add"; after the copy "No changes", 76 entries listed again. The whole restore is one command
and a plan to check it.

## Step 8 — The protections

```bash
aws s3api get-public-access-block --bucket <bucket>      # all four True
aws s3api get-bucket-encryption --bucket <bucket>        # AES256
# a request over plain HTTP (boto3 client with use_ssl=False) is refused: AccessDenied
```

## Done when

- [x] An ADR records the choice and what was rejected
- [x] Prod, staging, dev and the bucket's own folder keep their record in the bucket, versioned, encrypted and locked
- [x] The copy in the bucket equals the old file for each; plans show what they showed before
- [x] A second run is refused while the first holds the lock
- [x] A fresh clone plans staging with No changes (prod: the same two changes as the original checkout)
- [x] An earlier version restored once and written up in the runbook
- [x] Cost recorded
- [ ] Not seen: a stuck lock left by a crashed run (the runbook gives the command; none was left behind)
