output "role_arn" {
  description = "Goes into the repository variable AWS_PUBLISH_ROLE_ARN (a variable, not a secret and not a file in git: the ARN contains the account id)."
  value       = aws_iam_role.publish.arn
}
