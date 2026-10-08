output "database_private_ip" {
  description = "POSTGRES_HOST for every staging service."
  value       = module.database.private_ip
}

output "database_instance_id" {
  value = module.database.instance_id
}

output "database_data_volume_id" {
  description = "Needed by the teardown steps in docs/labs/lab-29-staging-environment.md: the volume is released from Terraform state and deleted by hand."
  value       = module.database.data_volume_id
}

output "database_ssm_parameter_paths" {
  description = "Read each with `aws ssm get-parameter --with-decryption --name <path>` yourself, never paste the value into chat (ADR-0026)."
  value       = module.database.ssm_parameter_paths
}

output "backup_bucket_name" {
  value = module.backups.bucket_name
}

output "node_asg_name" {
  value = module.node.asg_name
}

output "idle_stop_function_name" {
  description = "Invoke by hand to see its verdict: aws lambda invoke --function-name <this> out.json"
  value       = module.idle_stop.function_name
}
