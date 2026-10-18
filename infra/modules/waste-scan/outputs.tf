output "parameter_name" {
  description = "Where the scan leaves its findings. The environment's server copies this into the cluster."
  value       = aws_ssm_parameter.scan.name
}

output "parameter_arn" {
  description = "The node's role is allowed to read exactly this parameter (pass it to the iam module)."
  value       = aws_ssm_parameter.scan.arn
}

output "function_name" {
  value = aws_lambda_function.scan.function_name
}
