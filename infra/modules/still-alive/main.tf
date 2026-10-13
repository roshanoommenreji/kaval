# The role the "still alive" job borrows from GitHub (KAV-83, ADR-0052).
#
# `.github/workflows/still-alive.yml` asks one question every five minutes: does the app-server
# group want a server, or have one? While production is parked the answer is no, and the job sends
# the outside "still alive" ping on production's behalf (no poller is running to send it). It runs
# from GitHub, not from inside AWS, so it keeps working when AWS is parked or broken. This is the
# pass it carries, and it can only LOOK:
#   - the trust policy is the same as the other two roles': only a job from this repository on this
#     branch (a scheduled run counts as the default branch), nothing else;
#   - it may describe auto-scaling groups. That API has no resource-level permission, so the
#     resource is "*"; it is read-only and names servers, not data. It cannot change a group, start
#     or stop anything, or read a bucket, a secret or a parameter.
#
# Separate from the backup check's role on purpose: the two checks answer different questions, and a
# role that can do less is worth the few lines it costs.
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

resource "aws_iam_role" "still_alive" {
  name                 = "${var.name_prefix}-still-alive"
  assume_role_policy   = data.aws_iam_policy_document.trust.json
  max_session_duration = 3600
  tags                 = var.tags
}

data "aws_iam_policy_document" "look" {
  statement {
    sid       = "SeeWhetherTheServerGroupIsRunning"
    effect    = "Allow"
    actions   = ["autoscaling:DescribeAutoScalingGroups"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "look" {
  name   = "look-at-the-server-group"
  role   = aws_iam_role.still_alive.id
  policy = data.aws_iam_policy_document.look.json
}
