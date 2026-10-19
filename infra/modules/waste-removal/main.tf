# The removal worker (KAV-95, ADR-0063): the only thing in Kaval that can delete waste in AWS.
#
# A person approves an order; the executor writes it into the cluster; the server's timer copies it
# into ONE parameter; a parameter-change rule wakes this function; it re-checks AWS itself and acts;
# it writes its answer into ANOTHER parameter, which the timer copies back. No program in the
# cluster holds an AWS login (ADR-0060).
#
#   - The login is narrow and every changing verb is fenced in IAM as well as in the code:
#       * make a backup copy of a disk labelled Project=kaval, in this Env, not Terraform's
#       * label a disk with KavalRemoval and nothing else
#       * delete a disk only if it carries Project=kaval, this Env, not Terraform's, AND has already
#         been marked (the KavalRemoval label exists): IAM itself refuses an unmarked delete
#       * delete a backup copy labelled Project=kaval and not Terraform's
#     It cannot stop, terminate, detach, release, copy, share, modify or create anything else. The
#     tests fail if the role file ever names another verb.
#   - Looking and acting never share a login: this role is not the scan's (infra/modules/waste-scan).
#   - Off with one switch (`enabled`): the function refuses every order with a fixed word.
#
# Cost: Lambda, the rule, the schedule and standard parameters are free at this size. Backup copies
# cost about $0.05 per GB-month while they exist (30 days). See docs/cost/budget-plan.md.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws     = { source = "hashicorp/aws", version = "~> 5.0" }
    archive = { source = "hashicorp/archive", version = "~> 2.4" }
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  orders_name   = "/kaval/${var.name_prefix}/removal-orders"
  result_name   = "/kaval/${var.name_prefix}/removal-result"
  function_name = "${var.name_prefix}-waste-removal"
  env_name      = trimprefix(var.name_prefix, "kaval-")
  volume_arn    = "arn:aws:ec2:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:volume/*"
  snapshot_arn  = "arn:aws:ec2:${data.aws_region.current.name}::snapshot/*"
}

data "archive_file" "removal" {
  type        = "zip"
  source_file = "${path.module}/lambda/waste_removal.py"
  output_path = "${path.module}/.build/waste_removal.zip"
}

# The two hand-over parameters. Terraform makes them with an empty placeholder and never touches the
# value again: the server's timer writes the orders, the function writes the results.
resource "aws_ssm_parameter" "orders" {
  name  = local.orders_name
  type  = "String"
  value = jsonencode({ orders = [] })
  tags  = var.tags

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_ssm_parameter" "result" {
  name  = local.result_name
  type  = "String"
  value = jsonencode({ results = [] })
  tags  = var.tags

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_iam_role" "removal" {
  name = local.function_name
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = var.tags
}

data "aws_iam_policy_document" "removal" {
  # Looking: these two cannot be narrowed by tag in IAM.
  statement {
    sid       = "Look"
    effect    = "Allow"
    actions   = ["ec2:DescribeSnapshots", "ec2:DescribeVolumes"]
    resources = ["*"]
  }

  # A backup copy of a Kaval disk of this environment that Terraform does not own.
  statement {
    sid       = "BackupOfAKavalDisk"
    effect    = "Allow"
    actions   = ["ec2:CreateSnapshot"]
    resources = [local.volume_arn]
    condition {
      test     = "StringEquals"
      variable = "ec2:ResourceTag/Project"
      values   = ["kaval"]
    }
    condition {
      test     = "StringEquals"
      variable = "ec2:ResourceTag/Env"
      values   = [local.env_name]
    }
    condition {
      test     = "StringNotEquals"
      variable = "ec2:ResourceTag/ManagedBy"
      values   = ["terraform"]
    }
  }

  # ...and the new copy must be born labelled as Kaval's own work.
  statement {
    sid       = "TheNewCopyIsLabelled"
    effect    = "Allow"
    actions   = ["ec2:CreateSnapshot"]
    resources = [local.snapshot_arn]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/Project"
      values   = ["kaval"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/ManagedBy"
      values   = ["kaval-lambda"]
    }
  }

  # Labels given at the moment the copy is made (and only then).
  statement {
    sid       = "LabelTheNewCopy"
    effect    = "Allow"
    actions   = ["ec2:CreateTags"]
    resources = [local.snapshot_arn]
    condition {
      test     = "StringEquals"
      variable = "ec2:CreateAction"
      values   = ["CreateSnapshot"]
    }
  }

  # The mark: one label, on a Kaval disk of this environment that Terraform does not own.
  statement {
    sid       = "MarkTheDisk"
    effect    = "Allow"
    actions   = ["ec2:CreateTags"]
    resources = [local.volume_arn]
    condition {
      test     = "StringEquals"
      variable = "ec2:ResourceTag/Project"
      values   = ["kaval"]
    }
    condition {
      test     = "StringEquals"
      variable = "ec2:ResourceTag/Env"
      values   = [local.env_name]
    }
    condition {
      test     = "StringNotEquals"
      variable = "ec2:ResourceTag/ManagedBy"
      values   = ["terraform"]
    }
    condition {
      test     = "ForAllValues:StringEquals"
      variable = "aws:TagKeys"
      values   = ["KavalRemoval"]
    }
  }

  # The delete: only a disk that was already marked. IAM refuses an unmarked one even if the code did not.
  statement {
    sid       = "DeleteAMarkedDisk"
    effect    = "Allow"
    actions   = ["ec2:DeleteVolume"]
    resources = [local.volume_arn]
    condition {
      test     = "StringEquals"
      variable = "ec2:ResourceTag/Project"
      values   = ["kaval"]
    }
    condition {
      test     = "StringEquals"
      variable = "ec2:ResourceTag/Env"
      values   = [local.env_name]
    }
    condition {
      test     = "StringNotEquals"
      variable = "ec2:ResourceTag/ManagedBy"
      values   = ["terraform"]
    }
    condition {
      test     = "Null"
      variable = "ec2:ResourceTag/KavalRemoval"
      values   = ["false"]
    }
  }

  statement {
    sid       = "DeleteAKavalCopy"
    effect    = "Allow"
    actions   = ["ec2:DeleteSnapshot"]
    resources = [local.snapshot_arn]
    condition {
      test     = "StringEquals"
      variable = "ec2:ResourceTag/Project"
      values   = ["kaval"]
    }
    condition {
      test     = "StringNotEquals"
      variable = "ec2:ResourceTag/ManagedBy"
      values   = ["terraform"]
    }
  }

  # The two hand-over parameters, by exact name: read the orders, read and write the results.
  statement {
    sid       = "ReadTheOrders"
    effect    = "Allow"
    actions   = ["ssm:GetParameter"]
    resources = [aws_ssm_parameter.orders.arn]
  }

  statement {
    sid       = "ReadAndWriteTheResult"
    effect    = "Allow"
    actions   = ["ssm:GetParameter", "ssm:PutParameter"]
    resources = [aws_ssm_parameter.result.arn]
  }

  statement {
    sid       = "Logs"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.removal.arn}:*"]
  }
}

resource "aws_iam_role_policy" "removal" {
  name   = local.function_name
  role   = aws_iam_role.removal.id
  policy = data.aws_iam_policy_document.removal.json
}

resource "aws_cloudwatch_log_group" "removal" {
  name              = "/aws/lambda/${local.function_name}"
  retention_in_days = 14
  tags              = var.tags
}

resource "aws_lambda_function" "removal" {
  function_name    = local.function_name
  role             = aws_iam_role.removal.arn
  handler          = "waste_removal.handler"
  runtime          = "python3.12"
  filename         = data.archive_file.removal.output_path
  source_code_hash = data.archive_file.removal.output_base64sha256
  timeout          = 120

  environment {
    variables = {
      ENV_NAME         = local.env_name
      ORDERS_PARAMETER = local.orders_name
      RESULT_PARAMETER = local.result_name
      GRACE_HOURS      = tostring(var.grace_hours)
      ENABLED          = tostring(var.enabled)
    }
  }

  # The log group is made above so its retention applies and the function needs no right to create it.
  depends_on = [aws_cloudwatch_log_group.removal]
  tags       = var.tags
}

# ─────────────────────────────────────────────────────────────
# What wakes it: a new order in the parameter, and a quiet safety net
# ─────────────────────────────────────────────────────────────

resource "aws_cloudwatch_event_rule" "order" {
  name        = "${local.function_name}-order"
  description = "A new removal order was left in the parameter."
  event_pattern = jsonencode({
    source        = ["aws.ssm"]
    "detail-type" = ["Parameter Store Change"]
    detail = {
      name      = [local.orders_name]
      operation = ["Create", "Update"]
    }
  })
  tags = var.tags
}

resource "aws_cloudwatch_event_target" "order" {
  rule = aws_cloudwatch_event_rule.order.name
  arn  = aws_lambda_function.removal.arn
}

resource "aws_lambda_permission" "order" {
  statement_id  = "AllowExecutionFromTheOrderRule"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.removal.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.order.arn
}

# Every 6 hours it looks again, in case an event was missed. It does nothing unless an order has not
# been answered yet (the function skips answered orders), so it is safe and free.
resource "aws_iam_role" "scheduler" {
  name = "${local.function_name}-scheduler"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = var.tags
}

resource "aws_iam_role_policy" "scheduler" {
  name = "invoke-waste-removal"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["lambda:InvokeFunction"]
      Resource = [aws_lambda_function.removal.arn]
    }]
  })
}

resource "aws_scheduler_schedule" "safety_net" {
  name                = local.function_name
  group_name          = "default"
  schedule_expression = "rate(6 hours)"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.removal.arn
    role_arn = aws_iam_role.scheduler.arn

    retry_policy {
      maximum_retry_attempts       = 0
      maximum_event_age_in_seconds = 3600
    }
  }
}

resource "aws_lambda_permission" "scheduler" {
  statement_id  = "AllowExecutionFromScheduler"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.removal.function_name
  principal     = "scheduler.amazonaws.com"
  source_arn    = aws_scheduler_schedule.safety_net.arn
}
