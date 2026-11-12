output "role_arn" {
  description = "Goes into the repository secret AWS_BACKUP_WATCH_ROLE_ARN (a secret, not a variable and not a file in git: the ARN contains the account id, and GitHub prints a variable in public logs)."
  value       = aws_iam_role.watch.arn
}
