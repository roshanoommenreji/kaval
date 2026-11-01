output "parameter_arn" {
  description = "The node's role is allowed to read exactly this parameter (pass it to the iam module)."
  value       = aws_ssm_parameter.token.arn
}

output "parameter_name" {
  value = aws_ssm_parameter.token.name
}
