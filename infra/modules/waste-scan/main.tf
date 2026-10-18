# The daily waste scan (KAV-94, ADR-0062).
#
# A small function that looks, once a day, for the four kinds of waste Kaval can see (a disk or fixed
# address attached to nothing, a backup copy past its retention, a staging/dev server left running)
# and for Kaval resources missing their labels. It writes what it found into ONE parameter. The
# environment's server copies that parameter into the cluster, where the cost reader turns the
# findings into signals. The function finds and reports; it never changes anything.
#
#   - Read-only on the account: four Describe calls (none can be scoped by tag in IAM, so "*") and
#     nothing that stops, deletes, detaches, snapshots or labels. The only thing it can write is its
#     own parameter, by exact name. The tests fail if a changing verb ever appears in this file.
#   - It runs on a schedule of its own, not on the environment's server, so it works while the
#     server is parked and a parked production still has an up-to-date answer waiting.
#   - Looking and acting never share a login: removing anything is KAV-95, with its own role.
#
# Cost: Lambda and EventBridge Scheduler are free at 30 runs a month; the Describe calls are free; a
# standard parameter is free. See docs/cost/budget-plan.md.

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
  parameter_name = "/kaval/${var.name_prefix}/waste-scan"
  function_name  = "${var.name_prefix}-waste-scan"
  # The value the Env label carries for this environment, same rule as the node module.
  env_name = trimprefix(var.name_prefix, "kaval-")
}

data "archive_file" "waste_scan" {
  type        = "zip"
  source_file = "${path.module}/lambda/waste_scan.py"
  output_path = "${path.module}/.build/waste_scan.zip"
}

# The hand-over. Terraform makes it with a placeholder and never touches the value again; the
# function overwrites it daily. "pending" tells the reader "no scan has run yet", which is not a
# failure.
resource "aws_ssm_parameter" "scan" {
  name  = local.parameter_name
  type  = "String"
  value = jsonencode({ status = "pending" })
  tags  = var.tags

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_iam_role" "scan" {
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

data "aws_iam_policy_document" "scan" {
  # Read-only. None of these can be scoped to a tag in IAM.
  statement {
    sid    = "Look"
    effect = "Allow"
    actions = [
      "ec2:DescribeAddresses",
      "ec2:DescribeInstances",
      "ec2:DescribeSnapshots",
      "ec2:DescribeVolumes",
    ]
    resources = ["*"]
  }

  # The one thing it may write, and read back for the two-scan rule.
  statement {
    sid       = "OwnHandOverParameter"
    effect    = "Allow"
    actions   = ["ssm:GetParameter", "ssm:PutParameter"]
    resources = [aws_ssm_parameter.scan.arn]
  }

  statement {
    sid       = "Logs"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.scan.arn}:*"]
  }
}

resource "aws_iam_role_policy" "scan" {
  name   = local.function_name
  role   = aws_iam_role.scan.id
  policy = data.aws_iam_policy_document.scan.json
}

resource "aws_cloudwatch_log_group" "scan" {
  name              = "/aws/lambda/${local.function_name}"
  retention_in_days = 14
  tags              = var.tags
}

resource "aws_lambda_function" "scan" {
  function_name    = local.function_name
  role             = aws_iam_role.scan.arn
  handler          = "waste_scan.handler"
  runtime          = "python3.12"
  filename         = data.archive_file.waste_scan.output_path
  source_code_hash = data.archive_file.waste_scan.output_base64sha256
  timeout          = 120

  environment {
    variables = {
      ENV_NAME       = local.env_name
      PARAMETER_NAME = local.parameter_name
    }
  }

  # The log group is made above so its retention applies and the function needs no right to create it.
  depends_on = [aws_cloudwatch_log_group.scan]
  tags       = var.tags
}

# ─────────────────────────────────────────────────────────────
# The clock: once a day, 03:15 UTC (08:45 in India)
# ─────────────────────────────────────────────────────────────

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
  name = "invoke-waste-scan"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["lambda:InvokeFunction"]
      Resource = [aws_lambda_function.scan.arn]
    }]
  })
}

resource "aws_scheduler_schedule" "scan" {
  name                = local.function_name
  group_name          = "default"
  schedule_expression = "cron(15 3 * * ? *)"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.scan.arn
    role_arn = aws_iam_role.scheduler.arn

    # A scan that could not read the account is tried again, twice, then left to the reader's
    # "no good scan for 36 hours" alarm.
    retry_policy {
      maximum_retry_attempts       = 2
      maximum_event_age_in_seconds = 3600
    }
  }
}

resource "aws_lambda_permission" "scheduler" {
  statement_id  = "AllowExecutionFromScheduler"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.scan.function_name
  principal     = "scheduler.amazonaws.com"
  source_arn    = aws_scheduler_schedule.scan.arn
}
