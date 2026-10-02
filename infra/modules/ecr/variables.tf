variable "repository_names" {
  description = "One ECR repo per service image."
  type        = list(string)
  default     = ["gateway", "agent", "executor", "collector"]
}

variable "untagged_expire_days" {
  description = "Untagged images (superseded builds) are cleaned up; tagged sha-* images are kept forever — they're the audit trail of what was ever promoted."
  type        = number
  default     = 7
}
