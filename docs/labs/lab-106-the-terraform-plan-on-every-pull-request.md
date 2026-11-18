# Lab 106 — The Terraform plan on every pull request

**Phase:** 8 (hardening) · **Story:** `KAV-89` · **Decision:** [ADR-0104](../adr/0104-a-pull-request-shows-its-terraform-plan-through-a-read-only-role.md) · **Status:** built 2026-11-18; applied and proven live: *see Part 5 (filled in after the apply)*

## What you will build

A check on every pull request that changes `infra/`: it works out what Terraform would do to production and
to the state folder, and shows a short table on the run's summary page. Added, changed, replaced, destroyed,
and the **names** of the settings that change, never their values. Nothing is applied.

You need the repository, Terraform 1.11 or newer, the owner's AWS login, and the GitHub CLI logged in.

## Why

An infrastructure change was judged by `terraform validate` (does it parse?) and a plan on the laptop after
the merge. Seeing "this will replace the database server" before the merge is the point of a plan in the
pull request. Real companies do exactly this (Atlantis, GitHub Actions with a read-only cloud role).

Three things make it delicate here, and each has an answer:

- **The plan reads Terraform's record, which holds passwords and tokens.** So the role is read-only, can be
  assumed only by a pull request of this repository, and reads two files, not the bucket.
- **The repository is public, so logs are public.** A plan's text holds the account number in every ARN. So
  the plan's text goes to a file and never to the log; a script prints names only.
- **Production's private settings (alert email, server key) are not in git.** So they sit in one Parameter
  Store entry the owner writes.

## Part 1 — the role (Terraform)

`infra/modules/plan-reader/` builds `kaval-ci-plan`. The trust is one line that matters:

```hcl
values = ["repo:${var.github_repo_claim}:pull_request"]   # StringEquals: no wildcard
```

The policy has five read actions (`ListBucket`, `GetObject` on the two records, `ssm:GetParameter` on two named
places, `iam:GetOpenIDConnectProvider`, `ec2:DescribeSubnets`) and no write. `infra/envs/state/main.tf` calls it
with the bucket and the two record names. It is in the `state` folder so it outlives production's parking.

Apply (the owner's go, plan shown first, **2 to add**):

```bash
scripts/ops/tf-init.sh state
terraform -chdir=infra/envs/state plan -out=plan.tfplan
terraform -chdir=infra/envs/state apply plan.tfplan
```

## Part 2 — the two things only the owner does

The role's address holds the account number, so it is a repository **secret**, and it is set by the owner:

```bash
gh secret set AWS_PLAN_ROLE_ARN --body "$(terraform -chdir=infra/envs/state output -raw plan_role_arn)"
```

Production's settings: one Parameter Store entry holding the lines of `infra/envs/prod/terraform.tfvars`
(without `profile`; the workflow supplies none). Write it from your own file, so the value never passes
through a chat or a file in git:

```bash
aws ssm put-parameter --name /kaval/plan/prod-tfvars --type String --overwrite \
  --value "$(grep -v -E '^[[:space:]]*(profile|app_node_desired_capacity)[[:space:]]*=' infra/envs/prod/terraform.tfvars)"
```

If either is missing, the check says "Not run: the repository secret is not set" or fails at the settings step,
and the merge is not blocked.

## Part 3 — the workflow

`.github/workflows/terraform-plan.yml`, trigger `pull_request` on `infra/**` only:

1. Refuses a pull request from a fork, and one made by Dependabot.
2. Logs in to AWS with the secret (account number hidden, ADR-0091).
3. Reads the bucket's name and production's settings, hides both from the log.
4. `terraform init`, then `terraform plan -refresh=false -lock=false`, all output to files.
5. `terraform show -json`, then `scripts/ops/plan_summary.py` prints the table into the summary page.

`-refresh=false` means the plan compares the code with the record and does not look at the live account (so the
role needs no wide `Describe` permissions). `-lock=false` means it never writes the lock file (so the role has
no write permission). Drift made by hand is not shown.

## Part 4 — the tests

```bash
python -m pytest scripts/ops/test_plan_summary.py scripts/dev/test_terraform_plan_workflow.py -q
```

The negative cases are the point: a trust widened to `StringLike`, to `main`, a role that can write, a
workflow that triggers on `pull_request_target`, that skips the fork check, reads the role from a variable,
drops `-refresh=false` or prints the plan's text. Each must fail the test.

## Part 5 — the live proof (filled in after the apply)

*To be recorded: the apply, the first pull-request run, the permission check (can read the record, cannot
write it), and what the first live run needed.*
