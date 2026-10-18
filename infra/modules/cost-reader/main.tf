# The login the cost reader borrows (KAV-92, ADR-0060).
#
# Once a day a small job on the server (`kaval-cost-pull`, in the node's start-up script) asks AWS
# Cost Explorer what the account spent. It does that as THIS role, not as the server's own role, so
# the one permission that exists here is "read costs" and the server's other powers (pull images,
# read its secrets, write backups) are not in play for that call.
#
#   - ce:GetCostAndUsage and nothing else. Cost Explorer has no resource-level permissions, so the
#     only fence is the verb: this role cannot read a bucket, a secret or an instance, and cannot
#     write, change or delete anything in the account. `ce:GetCostAndUsage` is a read.
#   - only this environment's server can borrow it: the trust names the node's role exactly. A pod
#     in the cluster cannot reach the server's role at all (IMDSv2 only, one hop), so it cannot
#     borrow this one either.
#   - separate from the executor's future cost actions (Phase 6, KAV-95), which will get their own
#     role with an allow-list and a permission boundary. Looking and acting never share a login.
#
# Cost: IAM roles are free. Each Cost Explorer request is $0.01, capped at 60 a month by the job.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

data "aws_iam_policy_document" "trust" {
  statement {
    sid     = "OnlyThisEnvironmentsServer"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = [var.node_role_arn]
    }
  }
}

resource "aws_iam_role" "reader" {
  name                 = "${var.name_prefix}-cost-reader"
  assume_role_policy   = data.aws_iam_policy_document.trust.json
  max_session_duration = 3600
  tags                 = var.tags
}

data "aws_iam_policy_document" "reader" {
  statement {
    sid       = "ReadCosts"
    effect    = "Allow"
    actions   = ["ce:GetCostAndUsage"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "reader" {
  name   = "read-costs"
  role   = aws_iam_role.reader.id
  policy = data.aws_iam_policy_document.reader.json
}

# The other half: the server's role may borrow this role, and no other role.
data "aws_iam_policy_document" "node_may_borrow" {
  statement {
    sid       = "BorrowTheCostReader"
    effect    = "Allow"
    actions   = ["sts:AssumeRole"]
    resources = [aws_iam_role.reader.arn]
  }
}

resource "aws_iam_role_policy" "node_may_borrow" {
  name   = "borrow-cost-reader"
  role   = var.node_role_name
  policy = data.aws_iam_policy_document.node_may_borrow.json
}
