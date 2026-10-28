output "function_name" {
  description = "Try it by hand: aws lambda invoke --function-name <this> out.json (set dry_run first when testing)."
  value       = aws_lambda_function.reaper.function_name
}

output "log_group" {
  description = "Where to read what the timer did."
  value       = aws_cloudwatch_log_group.reaper.name
}
