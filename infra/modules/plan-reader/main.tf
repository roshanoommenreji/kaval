# The role a pull request borrows from GitHub to show what a Terraform change would do (KAV-89, ADR-0104).
#
# `.github/workflows/terraform-plan.yml` runs `terraform plan` for production and the state folder on a
# pull request that touches infra/. It needs Terraform's record of what exists, which holds generated
# database passwords and the Slack tokens (ADR-0058), so this role is a role that can read secrets. What
# keeps it safe:
#   - the trust policy accepts one thing only: a job of this repository started by a pull request
#     (the token's sub is `repo:<repository>:pull_request`). StringEquals, no wildcard: not main, not a
#     tag, not a branch push, not another repository. A pull request from a fork gets no token at all
#     (GitHub's rule), and Dependabot's jobs cannot read the repository secret that holds this role's
#     address, so neither can use it;
#   - it can only READ: the two state objects it plans (never write, so it holds no lock, and it cannot
#     change the record), one folder of Parameter Store, the public image-name parameter, the GitHub
#     sign-in provider's description and the subnet list. It cannot read the backups or any other bucket,
#     and it has no way to change anything in the account;
#   - the plan is run without refreshing (`-refresh=false`): it compares the code with the record, so it
#     needs no permission to look at the live resources. Drift made by hand is a separate check.
#
# Separate from the publishing and backup roles on purpose: a job that can push images, or that watches
# backups, must not also be able to read the record.
#
# Cost: IAM roles are free.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

data "aws_iam_policy_document" "trust" {
  statement {
    sid     = "OnlyThisRepoFromAPullRequest"
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # StringEquals, not StringLike: no wildcard, so no branch, tag, environment or other repository.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo_claim}:pull_request"]
    }
  }
}

resource "aws_iam_role" "plan" {
  name                 = "${var.name_prefix}-ci-plan"
  assume_role_policy   = data.aws_iam_policy_document.trust.json
  max_session_duration = 3600
  tags                 = var.tags
}

data "aws_iam_policy_document" "plan" {
  # Terraform's backend asks which records exist before it reads one. Names only.
  statement {
    sid       = "ListTheStateBucket"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [var.state_bucket_arn]
  }

  # The two records the plan reads, nothing else in the bucket. No s3:PutObject: the plan is run with
  # -lock=false, so it never writes the lock file, and it cannot write the record.
  statement {
    sid       = "ReadTheTwoRecords"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = [for k in var.state_keys : "${var.state_bucket_arn}/${k}"]
  }

  # The bucket's name (Terraform's own parameter) and the settings the plan needs that are not in git
  # (the alert email, the server key). Written by the owner by hand; this role cannot write them.
  statement {
    sid     = "ReadThePlanSettings"
    effect  = "Allow"
    actions = ["ssm:GetParameter"]
    resources = [
      "arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter/kaval/tfstate/bucket",
      "arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter/kaval/plan/*",
    ]
  }

  # The newest Amazon Linux image name, which the server modules look up while planning. A public
  # parameter owned by AWS (no account in its address).
  statement {
    sid       = "ReadThePublicImageName"
    effect    = "Allow"
    actions   = ["ssm:GetParameter"]
    resources = ["arn:aws:ssm:${data.aws_region.current.name}::parameter/aws/service/ami-amazon-linux-latest/*"]
  }

  statement {
    sid       = "ReadTheGitHubSignIn"
    effect    = "Allow"
    actions   = ["iam:GetOpenIDConnectProvider"]
    resources = [data.aws_iam_openid_connect_provider.github.arn]
  }

  # Terraform finds the GitHub sign-in provider by its address, which means listing the account's
  # providers first (found by the first live run, KAV-89). A list has no resource-level permission, so
  # the resource is "*"; it names the sign-in providers and nothing else.
  statement {
    sid       = "ListSignInProviders"
    effect    = "Allow"
    actions   = ["iam:ListOpenIDConnectProviders"]
    resources = ["*"]
  }

  # The database module looks up the subnet it sits in. This API has no resource-level permission, so
  # the resource is "*"; it is read-only and names networks, not data.
  statement {
    sid       = "ReadSubnets"
    effect    = "Allow"
    actions   = ["ec2:DescribeSubnets"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "plan" {
  name   = "read-the-record-and-plan"
  role   = aws_iam_role.plan.id
  policy = data.aws_iam_policy_document.plan.json
}
