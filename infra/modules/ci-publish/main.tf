# The role GitHub Actions borrows to publish images (KAV-61, ADR-0030).
#
# release.yml builds an image from a merged commit and pushes it to ECR. That needs AWS to
# accept the push, and the alternatives are both worse: a stored access key (never expires,
# lives in GitHub secrets) or pushing from a laptop (the artifact then depends on one machine).
# So GitHub proves who it is with OIDC, and AWS hands back a pass that lasts an hour.
#
# The role is deliberately the smallest thing that can do the job:
#   - it can be assumed ONLY by a job from this repository, running on this branch. A fork, a
#     pull request or another branch gets "not authorized", which is the security of the whole
#     arrangement (the trust policy below);
#   - once assumed, it can only upload layers, put images into the Kaval repositories and read
#     them back (to sign them and check the signature, KAV-138). It cannot delete, read secrets,
#     start servers or touch anything else. ECR tags are immutable (modules/ecr), so it cannot
#     overwrite an image that already exists either.
#
# What this does NOT create: the GitHub OIDC provider. That is one per AWS account and this
# account already has it (created by the stock-trader project's Terraform), so it is looked up,
# never managed here -- creating it again would fail, and "fixing" it would break that project.
#
# Cost: IAM roles and OIDC are free.

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

    # StringEquals, not StringLike: no wildcard, so no other branch, tag or pull request
    # (whose sub is repo:...:pull_request) can ever match. The repository part is the immutable
    # form with numeric ids (found when the first release run was refused: KAV-61, Lab 32).
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo_claim}:ref:refs/heads/${var.branch}"]
    }
  }
}

resource "aws_iam_role" "publish" {
  name                 = "${var.name_prefix}-ci-publish"
  assume_role_policy   = data.aws_iam_policy_document.trust.json
  max_session_duration = 3600
  tags                 = var.tags
}

data "aws_iam_policy_document" "publish" {
  # Logging in to the registry is account-wide by design; it grants no access by itself.
  statement {
    sid       = "RegistryLogin"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  # Push and check, on the Kaval repositories only. No delete, no pull of other accounts' images.
  statement {
    sid    = "PushKavalImages"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage",
      "ecr:DescribeImages",
    ]
    resources = var.ecr_repository_arns
  }

  # Read, on the same five repositories and nothing else (KAV-138, ADR-0090). Signing an image
  # means looking it up first, and checking the signature afterwards means reading it back
  # (the signature is stored in the registry beside the image). Still no delete, and no read of
  # any other repository: a stolen pass could copy a public repository's images, nothing more.
  statement {
    sid    = "ReadKavalImagesToSignAndCheckThem"
    effect = "Allow"
    actions = [
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = var.ecr_repository_arns
  }
}

resource "aws_iam_role_policy" "publish" {
  name   = "push-kaval-images"
  role   = aws_iam_role.publish.id
  policy = data.aws_iam_policy_document.publish.json
}
