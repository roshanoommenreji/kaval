# The role the daily backup check borrows from GitHub (KAV-82, ADR-0049).
#
# `.github/workflows/backup-watch.yml` asks two questions once a day: which database dumps are in the
# backup bucket, and when was the app server running (so a parked production is not mistaken for a
# missed dump). It runs from GitHub, not from inside AWS, so it keeps working when AWS is broken or
# everything is parked. This is the pass it carries, and it can only LOOK:
#   - the trust policy is the same as the publishing role's: only a job from this repository on
#     this branch (a scheduled run counts as the default branch), nothing else;
#   - it may list the dump folder of the one backup bucket, not read a dump, write or delete anything;
#   - it may read the auto-scaling group's launch and terminate history, which names no data.
#
# Separate from the publishing role on purpose: a job that can push images must not also be able to
# look at the backups, and a check that can only look should not be able to push images.
#
# Cost: IAM roles are free.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

data "aws_iam_policy_document" "trust" {
  statement {
    sid     = "OnlyThisRepoOnThisBranch"
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

    # StringEquals, not StringLike: no wildcard, so no other branch, tag or pull request matches.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo_claim}:ref:refs/heads/${var.branch}"]
    }
  }
}

resource "aws_iam_role" "watch" {
  name                 = "${var.name_prefix}-backup-watch"
  assume_role_policy   = data.aws_iam_policy_document.trust.json
  max_session_duration = 3600
  tags                 = var.tags
}

data "aws_iam_policy_document" "watch" {
  # Names and sizes of the dumps, nothing inside them: no s3:GetObject.
  statement {
    sid       = "ListTheDumpFolder"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [var.backup_bucket_arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["postgres/", "postgres/*"]
    }
  }

  # When the app server ran. This API has no resource-level permission, so the resource is "*";
  # it is read-only and the history names instances, not data.
  statement {
    sid       = "ReadScalingHistory"
    effect    = "Allow"
    actions   = ["autoscaling:DescribeScalingActivities"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "watch" {
  name   = "look-at-backups-and-server-history"
  role   = aws_iam_role.watch.id
  policy = data.aws_iam_policy_document.watch.json
}
