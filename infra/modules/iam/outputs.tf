output "instance_profile_name" {
  value = aws_iam_instance_profile.node.name
}

# Needed by modules/cost-reader (KAV-92): it trusts this role by name and gives it the right to
# borrow the cost reader's role.
output "node_role_arn" {
  value = aws_iam_role.node.arn
}

output "node_role_name" {
  value = aws_iam_role.node.name
}
