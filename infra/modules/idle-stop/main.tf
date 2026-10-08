# Idle self-stop for an on-demand environment (ADR-0029).
#
# A Lambda on a 15-minute schedule. If nobody has touched the environment's servers for
# `idle_hours`, it scales the node's Auto Scaling Group to zero and stops the database server
# (after a pre-stop snapshot). It stops, never destroys: the disks survive, `make staging-up`
# resumes in minutes, and `make staging-down` is the deliberate full teardown.
#
# Why a Lambda and not a timer on the node (what the dev server does, ADR-0007): the node is
# disposable and may be mid-replacement, the database server is a separate machine, and giving
# the node the right to scale its own Auto Scaling Group would put an AWS write permission on
# the box that runs the workloads. Everything this needs, it can see from outside.
#
# Cost: Lambda and EventBridge Scheduler are free at ~2,900 invocations a month. See
# docs/cost/budget-plan.md.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws     = { source = "hashicorp/aws", version = "~> 5.0" }
    archive = { source = "hashicorp/archive", version = "~> 2.4" }
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

data "archive_file" "idle_stop" {
  type        = "zip"
  source_file = "${path.module}/lambda/idle_stop.py"
  output_path = "${path.module}/.build/idle_stop.zip"
}

resource "aws_iam_role" "idle_stop" {
  name = "${var.name_prefix}-idle-stop"
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

# Deliberately narrow: this role can park one environment, and nothing else.
data "aws_iam_policy_document" "idle_stop" {
  # Only this environment's group, not every group in the account.
  statement {
    sid     = "ScaleOwnAsgToZero"
    effect  = "Allow"
    actions = ["autoscaling:UpdateAutoScalingGroup"]
    resources = [
      "arn:aws:autoscaling:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:autoScalingGroup:*:autoScalingGroupName/${var.asg_name}",
    ]
  }

  # Read-only. None of these can be scoped to a tag in IAM.
  statement {
    sid    = "Look"
    effect = "Allow"
    actions = [
      "autoscaling:DescribeAutoScalingGroups",
      "ec2:DescribeInstances",
      "ec2:DescribeVolumes",
      "ssm:DescribeSessions",
      "ssm:ListCommands",
    ]
    resources = ["*"]
  }

  # Stop only, never terminate, and only this environment's tagged servers.
  statement {
    sid       = "StopOwnServers"
    effect    = "Allow"
    actions   = ["ec2:StopInstances"]
    resources = ["arn:aws:ec2:*:*:instance/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/${var.stop_tag_key}"
      values   = [var.stop_tag_value]
    }
  }

  statement {
    sid       = "SnapshotOwnVolumes"
    effect    = "Allow"
    actions   = ["ec2:CreateSnapshot"]
    resources = ["arn:aws:ec2:*:*:volume/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/${var.stop_tag_key}"
      values   = [var.stop_tag_value]
    }
  }

  # The snapshot being created does not exist yet when CreateSnapshot is evaluated, so tagging it
  # cannot be scoped by a resource tag. Scoped to "a snapshot this same call just created"
  # instead, the same shape as the budget hard stop (ADR-0008).
  statement {
    sid       = "CreateSnapshotItself"
    effect    = "Allow"
    actions   = ["ec2:CreateSnapshot"]
    resources = ["arn:aws:ec2:*:*:snapshot/*"]
  }

  statement {
    sid       = "TagNewSnapshots"
    effect    = "Allow"
    actions   = ["ec2:CreateTags"]
    resources = ["arn:aws:ec2:*:*:snapshot/*"]
    condition {
      test     = "StringEquals"
      variable = "ec2:CreateAction"
      values   = ["CreateSnapshot"]
    }
  }

  statement {
    sid       = "Logs"
    effect    = "Allow"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:*:*:*"]
  }
}

resource "aws_iam_role_policy" "idle_stop" {
  name   = "${var.name_prefix}-idle-stop"
  role   = aws_iam_role.idle_stop.id
  policy = data.aws_iam_policy_document.idle_stop.json
}

resource "aws_lambda_function" "idle_stop" {
  function_name    = "${var.name_prefix}-idle-stop"
  role             = aws_iam_role.idle_stop.arn
  handler          = "idle_stop.handler"
  runtime          = "python3.12"
  filename         = data.archive_file.idle_stop.output_path
  source_code_hash = data.archive_file.idle_stop.output_base64sha256
  timeout          = 60

  environment {
    variables = {
      ASG_NAME      = var.asg_name
      DATABASE_NAME = var.database_name
      IDLE_HOURS    = tostring(var.idle_hours)
      DRY_RUN       = var.dry_run ? "true" : "false"
    }
  }

  tags = var.tags
}

resource "aws_cloudwatch_log_group" "idle_stop" {
  name              = "/aws/lambda/${aws_lambda_function.idle_stop.function_name}"
  retention_in_days = 14
  tags              = var.tags
}

# ─────────────────────────────────────────────────────────────
# The clock
# ─────────────────────────────────────────────────────────────

resource "aws_iam_role" "scheduler" {
  name = "${var.name_prefix}-idle-stop-scheduler"
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
  name = "invoke-idle-stop"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["lambda:InvokeFunction"]
      Resource = [aws_lambda_function.idle_stop.arn]
    }]
  })
}

resource "aws_scheduler_schedule" "idle_stop" {
  name       = "${var.name_prefix}-idle-stop"
  group_name = "default"

  schedule_expression = "rate(${var.check_every_minutes} minutes)"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.idle_stop.arn
    role_arn = aws_iam_role.scheduler.arn
  }
}

resource "aws_lambda_permission" "scheduler" {
  statement_id  = "AllowExecutionFromScheduler"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.idle_stop.function_name
  principal     = "scheduler.amazonaws.com"
  source_arn    = aws_scheduler_schedule.idle_stop.arn
}
