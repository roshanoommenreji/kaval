variable "repository_names" {
  description = "One ECR repo per service image."
  type        = list(string)
  default     = ["gateway", "agent", "executor", "collector", "backup"]
}

variable "untagged_expire_days" {
  description = "Untagged images (failed pushes) are cleaned up after this many days."
  type        = number
  default     = 7
}

variable "keep_tagged_images" {
  description = "Tagged sha-* images kept per repository; older ones are deleted by ECR (KAV-68, ADR-0036). Large enough that a parked prod's pinned tag stays inside it."
  type        = number
  default     = 15

  validation {
    condition     = var.keep_tagged_images >= 5
    error_message = "Keep at least 5: rollback and a parked prod both need older tags to still exist."
  }
}
