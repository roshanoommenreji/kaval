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
