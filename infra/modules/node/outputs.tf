output "asg_name" {
  description = "Fed back into the budget module's hard-stop Lambda — see infra/envs/prod/main.tf."
  value       = aws_autoscaling_group.node.name
}

output "security_group_id" {
  value = aws_security_group.node.id
}
