# Budget guardrails.
#
# This module is provisioned BEFORE any compute exists. Nothing here can create
# a bill; everything here exists to stop one.
#
#   $18  -> email
#   $22  -> email
#   $24  -> Lambda scales the ASG to zero. The cluster stops.
#
# See docs/cost/budget-plan.md.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws     = { source = "hashicorp/aws", version = "~> 5.0" }
    archive = { source = "hashicorp/archive", version = "~> 2.4" }
  }
}

# ─────────────────────────────────────────────────────────────
# Notification channel
# ─────────────────────────────────────────────────────────────

resource "aws_sns_topic" "budget" {
  name = "${var.name_prefix}-budget-alarm"
  tags = var.tags
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.budget.arn
  protocol  = "email"
  endpoint  = var.alert_email
  # Confirm the subscription from your inbox — AWS will not send alerts until you do.
}

data "aws_iam_policy_document" "sns_allow_budgets" {
  statement {
    effect  = "Allow"
    actions = ["SNS:Publish"]
    principals {
      type        = "Service"
      identifiers = ["budgets.amazonaws.com"]
    }
    resources = [aws_sns_topic.budget.arn]
  }
}

resource "aws_sns_topic_policy" "budget" {
  arn    = aws_sns_topic.budget.arn
  policy = data.aws_iam_policy_document.sns_allow_budgets.json
}

# ─────────────────────────────────────────────────────────────
# The budget
# ─────────────────────────────────────────────────────────────

resource "aws_budgets_budget" "monthly" {
  name         = "${var.name_prefix}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_limit_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # First warning — actual spend.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = var.alert_1_usd
    threshold_type             = "ABSOLUTE_VALUE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  # Second warning — actual spend.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = var.alert_2_usd
    threshold_type             = "ABSOLUTE_VALUE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  # Forecast warning — catches a runaway before it has finished running away.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = var.monthly_limit_usd
    threshold_type             = "ABSOLUTE_VALUE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }

  # Hard stop — fires the Lambda.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = var.hard_stop_usd
    threshold_type             = "ABSOLUTE_VALUE"
    notification_type          = "ACTUAL"
    subscriber_sns_topic_arns  = [aws_sns_topic.budget.arn]
    subscriber_email_addresses = [var.alert_email]
  }

  tags = var.tags
}

# ─────────────────────────────────────────────────────────────
# Hard stop
# ─────────────────────────────────────────────────────────────

data "archive_file" "hard_stop" {
  type        = "zip"
  source_file = "${path.module}/lambda/hard_stop.py"
  output_path = "${path.module}/.build/hard_stop.zip"
}

resource "aws_iam_role" "hard_stop" {
  name = "${var.name_prefix}-budget-hard-stop"
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

# Deliberately narrow: this role can stop things, and nothing else.
data "aws_iam_policy_document" "hard_stop" {
  statement {
    sid    = "ScaleAsgToZero"
    effect = "Allow"
    actions = [
      "autoscaling:DescribeAutoScalingGroups",
      "autoscaling:UpdateAutoScalingGroup",
      "autoscaling:SetDesiredCapacity",
    ]
    resources = ["*"]
  }

  # Finding instances can't be scoped to a tag in IAM, but it only reads.
  statement {
    sid       = "FindInstances"
    effect    = "Allow"
    actions   = ["ec2:DescribeInstances"]
    resources = ["*"]
  }

  # Stop only, never terminate, and only instances carrying the project tag.
  statement {
    sid       = "StopProjectInstances"
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
    sid       = "Logs"
    effect    = "Allow"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:*:*:*"]
  }
}

resource "aws_iam_role_policy" "hard_stop" {
  name   = "${var.name_prefix}-budget-hard-stop"
  role   = aws_iam_role.hard_stop.id
  policy = data.aws_iam_policy_document.hard_stop.json
}

resource "aws_lambda_function" "hard_stop" {
  function_name    = "${var.name_prefix}-budget-hard-stop"
  role             = aws_iam_role.hard_stop.arn
  handler          = "hard_stop.handler"
  runtime          = "python3.12"
  filename         = data.archive_file.hard_stop.output_path
  source_code_hash = data.archive_file.hard_stop.output_base64sha256
  timeout          = 30

  environment {
    variables = {
      ASG_NAME       = var.asg_name
      DRY_RUN        = var.hard_stop_dry_run ? "true" : "false"
      STOP_INSTANCES = var.stop_tagged_instances ? "true" : "false"
      STOP_TAG_KEY   = var.stop_tag_key
      STOP_TAG_VALUE = var.stop_tag_value
    }
  }

  tags = var.tags
}

resource "aws_lambda_permission" "sns" {
  statement_id  = "AllowExecutionFromSNS"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.hard_stop.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.budget.arn
}

resource "aws_sns_topic_subscription" "hard_stop" {
  topic_arn = aws_sns_topic.budget.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.hard_stop.arn
}

resource "aws_cloudwatch_log_group" "hard_stop" {
  name              = "/aws/lambda/${aws_lambda_function.hard_stop.function_name}"
  retention_in_days = 14
  tags              = var.tags
}
