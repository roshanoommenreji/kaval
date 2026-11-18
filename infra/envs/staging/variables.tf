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

variable "node_instance_types" {
  description = "The server sizes staging's node may run on, best first (KAV-163, ADR-0103). Normally the module's own list (t4g.medium, then c6g.large). A proof session puts c6g.large first to show the whole system runs on the fallback size."
  type        = list(string)
  default     = ["t4g.medium", "c6g.large"]
}

variable "idle_hours" {
  description = "Hours with nobody on the staging servers before they are parked (ADR-0004 says four, ADR-0029). Fractions work for drills; the module refuses anything under 0.1."
  type        = number
  default     = 4
}
