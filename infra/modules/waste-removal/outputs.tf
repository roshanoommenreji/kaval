output "orders_parameter_name" {
  description = "Where the server's timer leaves the executor's order. The worker reads exactly this."
  value       = aws_ssm_parameter.orders.name
}

output "orders_parameter_arn" {
  description = "The node's role is allowed to WRITE exactly this parameter (pass it to the iam module)."
  value       = aws_ssm_parameter.orders.arn
}

output "result_parameter_name" {
  value = aws_ssm_parameter.result.name
}

output "result_parameter_arn" {
  description = "The node's role is allowed to READ exactly this parameter (add it to the iam module's read list)."
  value       = aws_ssm_parameter.result.arn
}

output "function_name" {
  value = aws_lambda_function.removal.function_name
}
