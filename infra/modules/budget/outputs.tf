output "sns_topic_arn" {
  description = "Budget alarm topic. Phase 4 wires the node module's ASG name into the hard stop."
  value       = aws_sns_topic.budget.arn
}

output "hard_stop_function_name" {
  description = "Invoke manually to test: aws lambda invoke --function-name <this> /dev/null"
  value       = aws_lambda_function.hard_stop.function_name
}

output "budget_name" {
  value = aws_budgets_budget.monthly.name
}

output "log_group" {
  description = "Where to read what the hard stop did."
  value       = aws_cloudwatch_log_group.hard_stop.name
}

output "nightly_auto_stop_schedule_name" {
  description = "aws scheduler get-schedule --name <this> --group-name default"
  value       = aws_scheduler_schedule.nightly_auto_stop.name
}
