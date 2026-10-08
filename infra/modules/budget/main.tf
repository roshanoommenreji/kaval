# Budget guardrails.
#
# This module is provisioned BEFORE any compute exists. Nothing here can create
# a bill; everything here exists to stop one.
#
#   $42  -> email                (alert_1_usd)
#   $46  -> email                (alert_2_usd)
#   $48  -> Lambda scales the ASG to zero and stops standalone Project=kaval
#           servers (hard_stop_usd). Figures are prod's, set in infra/envs/prod;
#           $18/$22/$24 until the ceiling rose to $40 (ADR-0008), then $30/$35/$38 until
#           it rose to $50 (ADR-0028).
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

  # Pre-stop snapshot (ADR-0008): a snapshot of the data volume right before it's stopped,
  # so a damaged resume always has something fresher than the last daily DLM snapshot.
  # Finding volumes can't be scoped to a tag in IAM either, same reasoning as FindInstances.
  statement {
    sid       = "FindVolumes"
    effect    = "Allow"
    actions   = ["ec2:DescribeVolumes"]
    resources = ["*"]
  }

  statement {
    sid       = "SnapshotProjectVolumes"
    effect    = "Allow"
    actions   = ["ec2:CreateSnapshot"]
    resources = ["arn:aws:ec2:*:*:volume/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/${var.stop_tag_key}"
      values   = [var.stop_tag_value]
    }
  }

  # Tagging the new snapshot needs its own grant — it doesn't exist yet when CreateSnapshot
  # is evaluated, so it can't be scoped by a resource tag. Scoped instead to "a snapshot this
  # same call just created", via the ec2:CreateAction condition key.
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

# ─────────────────────────────────────────────────────────────
# Nightly auto-stop (ADR-0008) — the third stop path, alongside `make down` and the $38
# hard stop. Invokes the SAME Lambda rather than duplicating its scale-to-zero/snapshot/stop
# logic: at 02:00 IST it is exactly "stop Project=kaval servers outside an ASG, and scale the
# ASG to zero" — identical to what the hard stop already does, just on a clock instead of a
# spend threshold. A forgotten `make down` is the only case where this actually changes
# anything; everything else is a no-op.
# ─────────────────────────────────────────────────────────────

resource "aws_iam_role" "nightly_auto_stop_scheduler" {
  name = "${var.name_prefix}-budget-nightly-auto-stop"
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

# Deliberately narrow: this role can invoke exactly one Lambda, and nothing else.
data "aws_iam_policy_document" "nightly_auto_stop_scheduler" {
  statement {
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.hard_stop.arn]
  }
}

resource "aws_iam_role_policy" "nightly_auto_stop_scheduler" {
  name   = "invoke-hard-stop"
  role   = aws_iam_role.nightly_auto_stop_scheduler.id
  policy = data.aws_iam_policy_document.nightly_auto_stop_scheduler.json
}

resource "aws_scheduler_schedule" "nightly_auto_stop" {
  name       = "${var.name_prefix}-nightly-auto-stop"
  group_name = "default"
  state      = var.nightly_auto_stop_enabled ? "ENABLED" : "DISABLED"

  # EventBridge Scheduler accepts an IANA timezone directly, so "02:00" means 02:00 IST without
  # a manual UTC offset conversion to get wrong.
  schedule_expression          = "cron(0 2 * * ? *)"
  schedule_expression_timezone = "Asia/Kolkata"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.hard_stop.arn
    role_arn = aws_iam_role.nightly_auto_stop_scheduler.arn
  }
}

resource "aws_lambda_permission" "scheduler" {
  statement_id  = "AllowExecutionFromScheduler"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.hard_stop.function_name
  principal     = "scheduler.amazonaws.com"
  source_arn    = aws_scheduler_schedule.nightly_auto_stop.arn
}
