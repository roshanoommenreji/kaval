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
  description = "On-Demand, same as prod (ADR-0028): a Spot capacity shortage (seen on 2026-10-06 and 2026-10-08) stalls a release, and staging exists for hours so the premium is pennies. true switches staging to Spot."
  type        = bool
  default     = false
}
