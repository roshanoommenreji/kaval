output "instance_id" {
  value = aws_instance.database.id
}

output "private_ip" {
  description = "What services connect to — POSTGRES_HOST. Private, not public: only the app node's security group can reach 5432, and SSM doesn't need a public IP to manage the instance."
  value       = aws_instance.database.private_ip
}

output "security_group_id" {
  value = aws_security_group.database.id
}

output "data_volume_id" {
  value = aws_ebs_volume.data.id
}

output "ssm_parameter_paths" {
  description = "Read with `aws ssm get-parameter --with-decryption`, never by Claude Code — same discipline as the Slack tokens (ADR-0026)."
  value = {
    for role, param in aws_ssm_parameter.db_password : role => param.name
  }
}

output "ssm_parameter_arns" {
  description = "Consumed by module.iam (KAV-56) to scope the node role's read access to exactly these parameters, not a wildcard."
  value       = [for p in aws_ssm_parameter.db_password : p.arn]
}
