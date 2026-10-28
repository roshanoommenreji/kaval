output "cluster_name" {
  value = module.cluster.cluster_name
}

output "kubeconfig_command" {
  description = "Run this to point kubectl at the cluster."
  value       = module.cluster.kubeconfig_command
}

output "executor_role_arn" {
  description = "The annotation the executor's service account carries. Used by the lab's HelmRelease, never committed (it contains the account number)."
  value       = module.cluster.executor_role_arn
}

output "load_balancer_controller_role_arn" {
  description = "Used by the controller's HelmRelease, never committed."
  value       = module.cluster.load_balancer_controller_role_arn
}

output "irsa_proof_parameter" {
  value = module.cluster.irsa_proof_parameter
}

output "reaper_function_name" {
  value = module.reaper.function_name
}
