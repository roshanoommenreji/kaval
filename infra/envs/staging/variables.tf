variable "region" {
  description = "Same region as prod (ADR-0003)."
  type        = string
  default     = "ap-south-1"
}

variable "profile" {
  description = "Named AWS CLI profile, the same one prod uses."
  type        = string
  default     = "kaval"
}

variable "ssh_public_key" {
  description = "For ec2-user on the staging node and database server. SSH only works through the SSM tunnel; no port is open."
  type        = string
}

variable "node_desired_capacity" {
  description = "1 to run the staging node, 0 to scale it away without destroying anything."
  type        = number
  default     = 1
}

variable "node_spot" {
  description = "Spot by default. false launches On-Demand, the escape hatch found in KAV-32 Lab 28 for when Spot capacity isn't available."
  type        = bool
  default     = true
}
