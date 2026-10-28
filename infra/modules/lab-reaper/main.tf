# The EKS lab's self-destruct timer (Phase 7, KAV-101, ADR-0067). What it does and why it is a
# Lambda and not Terraform: lambda/reaper.py. Lambda and EventBridge Scheduler are free at ~2,900
# invocations a month (docs/cost/budget-plan.md).

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
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name
}

data "archive_file" "reaper" {
  type        = "zip"
  source_file = "${path.module}/lambda/reaper.py"
  output_path = "${path.module}/.build/reaper.zip"
}

resource "aws_iam_role" "reaper" {
  name = "${var.name_prefix}-reaper"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# Deliberately narrow: this role can take down one named cluster and the load balancer controller's
# leftovers for it, and nothing else.
data "aws_iam_policy_document" "reaper" {
  statement {
    sid    = "OwnClusterOnly"
    effect = "Allow"
    actions = [
      "eks:DescribeCluster",
      "eks:ListNodegroups",
      "eks:DescribeNodegroup",
      "eks:UpdateNodegroupConfig",
      "eks:DeleteNodegroup",
      "eks:DeleteCluster",
    ]
    resources = [
      "arn:aws:eks:${local.region}:${local.account_id}:cluster/${var.cluster_name}",
      "arn:aws:eks:${local.region}:${local.account_id}:nodegroup/${var.cluster_name}/*/*",
    ]
  }

  # Read-only. None of these can be limited by tag in IAM.
  statement {
    sid    = "Look"
    effect = "Allow"
    actions = [
      "elasticloadbalancing:DescribeLoadBalancers",
      "elasticloadbalancing:DescribeTargetGroups",
      "elasticloadbalancing:DescribeTags",
      "ec2:DescribeSecurityGroups",
    ]
    resources = ["*"]
  }

  # Delete only what the controller tagged with THIS cluster's name.
  statement {
    sid    = "DeleteOwnLoadBalancersAndTargets"
    effect = "Allow"
    actions = [
      "elasticloadbalancing:DeleteLoadBalancer",
      "elasticloadbalancing:DeleteTargetGroup",
    ]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/elbv2.k8s.aws/cluster"
      values   = [var.cluster_name]
    }
  }

  statement {
    sid       = "DeleteOwnSecurityGroups"
    effect    = "Allow"
    actions   = ["ec2:DeleteSecurityGroup"]
    resources = ["arn:aws:ec2:${local.region}:${local.account_id}:security-group/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/elbv2.k8s.aws/cluster"
      values   = [var.cluster_name]
    }
  }

  statement {
    sid       = "Logs"
    effect    = "Allow"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:*:*:*"]
  }
}

resource "aws_iam_role_policy" "reaper" {
  name   = "${var.name_prefix}-reaper"
  role   = aws_iam_role.reaper.id
  policy = data.aws_iam_policy_document.reaper.json
}

resource "aws_lambda_function" "reaper" {
  function_name    = "${var.name_prefix}-reaper"
  role             = aws_iam_role.reaper.arn
  handler          = "reaper.handler"
  runtime          = "python3.12"
  filename         = data.archive_file.reaper.output_path
  source_code_hash = data.archive_file.reaper.output_base64sha256
  timeout          = 120

  environment {
    variables = {
      CLUSTER_NAME   = var.cluster_name
      SCALE_IN_HOURS = tostring(var.scale_in_hours)
      DELETE_HOURS   = tostring(var.delete_hours)
      DRY_RUN        = var.dry_run ? "true" : "false"
    }
  }
}

resource "aws_cloudwatch_log_group" "reaper" {
  name              = "/aws/lambda/${aws_lambda_function.reaper.function_name}"
  retention_in_days = 14
}

# ─────────────────────────────────────────────────────────────
# The clock
# ─────────────────────────────────────────────────────────────

resource "aws_iam_role" "scheduler" {
  name = "${var.name_prefix}-reaper-scheduler"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "scheduler" {
  name = "invoke-reaper"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["lambda:InvokeFunction"]
      Resource = [aws_lambda_function.reaper.arn]
    }]
  })
}

resource "aws_scheduler_schedule" "reaper" {
  name       = "${var.name_prefix}-reaper"
  group_name = "default"

  schedule_expression = "rate(${var.check_every_minutes} minutes)"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.reaper.arn
    role_arn = aws_iam_role.scheduler.arn
  }
}

# ─────────────────────────────────────────────────────────────
# Watching the watcher: an error, or no run at all for an hour, means the timer cannot be trusted.
# ─────────────────────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "errors" {
  count = var.alarm_topic_arn == "" ? 0 : 1

  alarm_name          = "${var.name_prefix}-reaper-errors"
  alarm_description   = "The EKS lab's self-destruct timer failed. The cluster may still be running at $0.10 an hour."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.reaper.function_name }
  statistic           = "Sum"
  period              = 900
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.alarm_topic_arn]
}

resource "aws_cloudwatch_metric_alarm" "silent" {
  count = var.alarm_topic_arn == "" ? 0 : 1

  alarm_name          = "${var.name_prefix}-reaper-silent"
  alarm_description   = "The EKS lab's self-destruct timer has not run for an hour."
  namespace           = "AWS/Lambda"
  metric_name         = "Invocations"
  dimensions          = { FunctionName = aws_lambda_function.reaper.function_name }
  statistic           = "Sum"
  period              = 900
  evaluation_periods  = 4
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [var.alarm_topic_arn]
}
