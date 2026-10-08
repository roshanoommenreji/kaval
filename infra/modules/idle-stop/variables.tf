variable "name_prefix" {
  description = "Names the Lambda, its roles and the schedule, e.g. kaval-staging."
  type        = string
}

variable "asg_name" {
  description = "The node's Auto Scaling Group. Scaled to zero when idle."
  type        = string
}

variable "database_name" {
  description = "The database server's Name tag, e.g. kaval-staging-database. Stopped when idle."
  type        = string
}

variable "idle_hours" {
  description = "Hours without a session or command on any of the environment's servers before it is parked. ADR-0004 says four. Fractions work, for drills."
  type        = number
  default     = 4

  validation {
    condition     = var.idle_hours >= 0.1
    error_message = "idle_hours below 0.1 (six minutes) would park an environment before its node has finished booting."
  }
}

variable "check_every_minutes" {
  description = "How often the Lambda looks. Parking is at most this late."
  type        = number
  default     = 15
}

variable "dry_run" {
  description = "Log what would be stopped and stop nothing."
  type        = bool
  default     = false
}

variable "stop_tag_key" {
  description = "IAM scopes ec2:StopInstances and ec2:CreateSnapshot to resources with this tag, so the function cannot touch another environment."
  type        = string
  default     = "Env"
}

variable "stop_tag_value" {
  type    = string
  default = "staging"
}

variable "tags" {
  type    = map(string)
  default = {}
}
