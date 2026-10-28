variable "name" {
  description = "The cluster's name and the prefix of everything around it, e.g. kaval-lab-eks. Also what the load balancer controller tags the load balancers it makes with, which is how the self-destruct timer and the sweep find them."
  type        = string
}

variable "kubernetes_version" {
  description = <<-EOT
    The EKS Kubernetes version. 1.35 matches what CI already checks the chart against
    (kubeconform -kubernetes-version 1.35.x) and is in standard support to 2027-03-27 (checked with
    `aws eks describe-cluster-versions` on 2026-10-28). A version past standard support costs $0.50 an
    hour for the control plane instead of $0.10, so never pick one of those.
  EOT
  type        = string
  default     = "1.35"
}

variable "vpc_id" {
  type = string
}

variable "subnet_ids" {
  description = "The public subnets the cluster and its servers live in (no NAT gateway, CLAUDE.md). At least two, in different availability zones; EKS insists."
  type        = list(string)
}

variable "api_allowed_cidrs" {
  description = "The only addresses that may reach the cluster's public API, e.g. [\"203.0.113.7/32\"] (the laptop). No default, and the whole internet is refused."
  type        = list(string)

  validation {
    condition     = length(var.api_allowed_cidrs) > 0 && !contains(var.api_allowed_cidrs, "0.0.0.0/0")
    error_message = "List your own address(es). 0.0.0.0/0 would open the cluster's API to the whole internet."
  }
}

variable "node_instance_type" {
  description = "Arm servers, because every image is linux/arm64 (CLAUDE.md). 4 GB each, like production's node."
  type        = string
  default     = "t4g.medium"
}

variable "node_count" {
  description = "Servers in the node group. Two: the chart's pods plus the add-ons do not fit one 4 GB server."
  type        = number
  default     = 2
}

variable "node_disk_gb" {
  type    = number
  default = 20
}

variable "ecr_repository_arns" {
  description = "The image repositories the servers may pull from. Nothing else in ECR."
  type        = list(string)
}

variable "executor_namespace" {
  description = "Where the chart is installed. Part of the executor's IRSA trust: only this namespace's service account may use the role."
  type        = string
  default     = "kaval-lab"
}

variable "executor_service_account" {
  description = "The name the chart gives the executor's service account: the release name plus -executor (templates/executor.yaml)."
  type        = string
  default     = "kaval-lab-executor"
}
