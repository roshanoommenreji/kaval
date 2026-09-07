variable "name_prefix" {
  description = "Prefix for all resource names."
  type        = string
  default     = "kaval"
}

variable "alert_email" {
  description = "Where budget alerts go. You must confirm the SNS subscription from your inbox."
  type        = string
}

variable "monthly_limit_usd" {
  description = "The ceiling. Everything else is a fraction of this."
  type        = number
  default     = 25
}

variable "alert_1_usd" {
  description = "First warning threshold."
  type        = number
  default     = 18
}

variable "alert_2_usd" {
  description = "Second warning threshold."
  type        = number
  default     = 22
}

variable "hard_stop_usd" {
  description = "At this figure the Lambda scales the ASG to zero."
  type        = number
  default     = 24
}

variable "asg_name" {
  description = <<-EOT
    Auto Scaling Group the hard stop will scale to zero.
    Empty during Phase 0 — no compute exists yet, and that is the point.
    Populate in Phase 4 when the node module creates the ASG.
  EOT
  type        = string
  default     = ""
}

variable "hard_stop_dry_run" {
  description = <<-EOT
    When true the Lambda logs what it would do without doing it.
    Keep true until you have watched it fire once and read the log.
  EOT
  type        = bool
  default     = true
}

variable "tags" {
  description = "Applied to every resource. Tagging is not optional here — the FinOps agent in Phase 7 reasons over these."
  type        = map(string)
  default = {
    Project   = "kaval"
    ManagedBy = "terraform"
    Purpose   = "cost-guardrail"
  }
}
