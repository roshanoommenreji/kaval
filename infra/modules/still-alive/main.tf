# The "still alive" alarm (KAV-83, ADR-0053).
#
# Everything Kaval does to notice trouble runs inside the system it watches, so a lost server is
# silent. This is the one watcher that is not inside it: a CloudWatch alarm that emails when the
# server's two-minute "still alive" datapoints (put by the node itself, infra/modules/node) stop
# while the server is meant to be running.
#
# One alarm, one rule, in plain words: ALARM when, for five five-minute periods in a row (25
# minutes), the app-server group WANTS a server and no datapoint arrived.
#
#     IF( FILL(wanted, 1) > 0  AND  FILL(heartbeat, 0) < 1 , 1 , 0 )
#
#   - a PARKED production (the group wants 0) is 0, so it is silent: no cover job, no ping from
#     anywhere, no stored address. This is why the group's own size is read instead of asking
#     whether anything is running: "wants a server and is quiet" covers a server that died, one
#     that never launched, and a poller that stopped, and "wants nothing" is the only way to be quiet.
#   - the FILL(...) calls decide what a MISSING reading means, on purpose. A missing heartbeat is
#     "0 beats" (silent), and a missing group size is "1" (wants a server), so if either reading
#     ever disappears the alarm leans toward ringing, never toward quiet. Tried against real, empty
#     CloudWatch data before it was written (Lab 50): parked 0, wants-and-silent 1, both missing 1.
#   - five periods in a row ride out the 2 min 10 s self-heal and the 5 to 8 minute bring-up.
#
# It shares AWS's own failure points (an outage of this region or of this account takes the alarm
# down with the server): that is the price of having no outside company, recorded in ADR-0053.
#
# Cost (ap-south-1 list prices, AWS Price List API 2026-10-13): one custom metric $0.30, the alarm's
# two metrics $0.20, the node's one PutMetricData call every two minutes about $0.22: about $0.72 a
# month, and probably $0 inside CloudWatch's always-free allowance. SNS e-mail is free at this size.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

resource "aws_sns_topic" "still_alive" {
  name = "${var.name_prefix}-still-alive"
  tags = var.tags
}

# Staging passes no address: a new subscription needs a click on a confirmation e-mail, and staging
# is rebuilt often. Its alarm state is read with `aws cloudwatch describe-alarms` instead.
resource "aws_sns_topic_subscription" "email" {
  count     = var.alert_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.still_alive.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_cloudwatch_metric_alarm" "still_alive" {
  alarm_name        = "${var.name_prefix}-still-alive"
  alarm_description = "The app-server group wants a server but Kaval's still-alive datapoints have stopped for ${var.silent_periods * var.period_seconds / 60} minutes. Runbook: docs/runbooks/still-alive-alarm.md"

  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  evaluation_periods  = var.silent_periods
  datapoints_to_alarm = var.silent_periods
  # Both readings are filled, so there is always an answer; "breaching" is belt and braces for the
  # case where CloudWatch returns no series at all.
  treat_missing_data = "breaching"

  metric_query {
    id          = "heartbeat"
    return_data = false
    metric {
      namespace   = "Kaval/Heartbeat"
      metric_name = "StillAlive"
      period      = var.period_seconds
      stat        = "Sum"
      dimensions  = { Environment = var.name_prefix }
    }
  }

  metric_query {
    id          = "wanted"
    return_data = false
    metric {
      namespace   = "AWS/AutoScaling"
      metric_name = "GroupDesiredCapacity"
      period      = var.period_seconds
      stat        = "Maximum"
      dimensions  = { AutoScalingGroupName = var.asg_name }
    }
  }

  metric_query {
    id          = "silent_while_wanted"
    expression  = "IF(FILL(wanted, 1) > 0 AND FILL(heartbeat, 0) < 1, 1, 0)"
    label       = "Wants a server and silent"
    return_data = true
  }

  alarm_actions = [aws_sns_topic.still_alive.arn]
  ok_actions    = [aws_sns_topic.still_alive.arn]
  tags          = var.tags
}
