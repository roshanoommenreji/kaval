# Re-exposes the budget module's outputs at the root level. A module's own
# outputs.tf only makes values available to whatever calls that module --
# `terraform output` at the root sees nothing unless the root explicitly
# re-declares them here.

output "hard_stop_function_name" {
  description = "Invoke manually to test: aws lambda invoke --function-name <this> --profile kaval /tmp/out.json"
  value       = module.budget.hard_stop_function_name
}

output "sns_topic_arn" {
  description = "Budget alarm topic. Phase 4 wires the node module's ASG name into the hard stop."
  value       = module.budget.sns_topic_arn
}

output "budget_name" {
  value = module.budget.budget_name
}

output "log_group" {
  description = "Where to read what the hard stop did: aws logs tail <this> --profile kaval --since 5m"
  value       = module.budget.log_group
}

output "database_private_ip" {
  description = "POSTGRES_HOST for every service. Read this, not a hardcoded IP, when building the out-of-band Kubernetes Secrets (KAV-32)."
  value       = module.database.private_ip
}

output "database_ssm_parameter_paths" {
  description = "Read each with `aws ssm get-parameter --with-decryption --name <path>` yourself -- never paste the decrypted value into chat (same discipline as the Slack tokens, ADR-0026)."
  value       = module.database.ssm_parameter_paths
}

output "backup_bucket_name" {
  value = module.backups.bucket_name
}

output "ci_publish_role_arn" {
  description = "For the repository variable AWS_PUBLISH_ROLE_ARN (gh variable set). Contains the account id, so it lives in GitHub, never in a file."
  value       = module.ci_publish.role_arn
}
