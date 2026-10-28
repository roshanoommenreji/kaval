output "cluster_name" {
  value = aws_eks_cluster.this.name
}

output "cluster_endpoint" {
  value = aws_eks_cluster.this.endpoint
}

output "node_group_name" {
  value = aws_eks_node_group.this.node_group_name
}

output "kubeconfig_command" {
  description = "Run this to point kubectl at the cluster."
  value       = "aws eks update-kubeconfig --name ${aws_eks_cluster.this.name} --region ${local.region} --profile kaval"
}

output "executor_role_arn" {
  description = "The annotation the executor's service account carries (eks.amazonaws.com/role-arn). Added by the lab's own deploy file, not by the chart."
  value       = aws_iam_role.irsa["executor"].arn
}

output "load_balancer_controller_role_arn" {
  value = aws_iam_role.irsa["lbc"].arn
}

output "irsa_proof_parameter" {
  description = "The one parameter the executor's role may read."
  value       = aws_ssm_parameter.irsa_proof.name
}
