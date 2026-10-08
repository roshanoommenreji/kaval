output "function_name" {
  description = "For `aws lambda invoke` by hand, and the CloudWatch log group /aws/lambda/<name>."
  value       = aws_lambda_function.idle_stop.function_name
}
