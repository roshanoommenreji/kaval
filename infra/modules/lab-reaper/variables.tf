variable "name_prefix" {
  description = "Names the function, its role and the schedule, e.g. kaval-lab-eks."
  type        = string
}

variable "cluster_name" {
  description = "The EKS cluster this timer watches. The function and its IAM role can touch this cluster and what carries its name, nothing else."
  type        = string
}

variable "scale_in_hours" {
  description = "Cluster age at which the servers are scaled to zero."
  type        = number
  default     = 4
}

variable "delete_hours" {
  description = "Cluster age at which everything is taken down. Six hours of control plane is $0.60."
  type        = number
  default     = 6

  validation {
    condition     = var.delete_hours <= 12
    error_message = "More than 12 hours defeats the point of an ephemeral lab: the control plane bills $0.10 an hour."
  }
}

variable "check_every_minutes" {
  description = "How often the function looks. The cluster lives at most this much past its deadline."
  type        = number
  default     = 15
}

variable "dry_run" {
  description = "Log what would be done and do nothing."
  type        = bool
  default     = false
}

variable "alarm_topic_arn" {
  description = "Optional. A topic that is told when the timer errors or stops running. Empty = no alarm (the logs still say what it did)."
  type        = string
  default     = ""
}
