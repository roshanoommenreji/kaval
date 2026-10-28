variable "region" {
  description = "Same region as everything else (ADR-0003)."
  type        = string
  default     = "ap-south-1"
}

variable "profile" {
  description = "Named AWS CLI profile, the same one prod uses."
  type        = string
  default     = "kaval"
}

variable "api_allowed_cidrs" {
  description = "The only addresses that may reach the cluster's API, e.g. [\"203.0.113.7/32\"]: the laptop's public address (`curl https://checkip.amazonaws.com`). The module refuses 0.0.0.0/0."
  type        = list(string)
}

variable "eks_registry_account_id" {
  description = "AWS's own registry account for the EKS add-on images in this region (see modules/eks-lab). Public, published by AWS per region; kept out of git because the repo's checks reject any 12-digit number."
  type        = string
}

variable "kubernetes_version" {
  description = "See modules/eks-lab: stay in standard support ($0.10 an hour, not $0.50)."
  type        = string
  default     = "1.35"
}

variable "node_count" {
  type    = number
  default = 2
}

variable "scale_in_hours" {
  description = "Cluster age at which the servers are scaled to zero."
  type        = number
  default     = 4
}

variable "delete_hours" {
  description = "Cluster age at which the self-destruct timer takes everything down (at most 12)."
  type        = number
  default     = 6
}

variable "reaper_dry_run" {
  description = "The timer logs what it would do and does nothing. For proving it on a young cluster."
  type        = bool
  default     = false
}

variable "alarm_topic_arn" {
  description = "Optional: a topic told when the timer errors or goes silent. Prod's budget topic works (`terraform -chdir=infra/envs/prod output`); leave empty to skip."
  type        = string
  default     = ""
}
