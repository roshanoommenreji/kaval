output "alarm_name" {
  description = "The alarm. Read its state with: aws cloudwatch describe-alarms --alarm-names <name>."
  value       = aws_cloudwatch_metric_alarm.still_alive.alarm_name
}

output "topic_arn" {
  description = "The SNS topic the alarm publishes to (alarm and back-to-normal)."
  value       = aws_sns_topic.still_alive.arn
}
