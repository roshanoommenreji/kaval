output "role_arn" {
  description = "Goes into the repository variable AWS_STILL_ALIVE_ROLE_ARN (a variable, not a secret and not a file in git: the ARN contains the account id). Setting it is what switches the job on."
  value       = aws_iam_role.still_alive.arn
}
