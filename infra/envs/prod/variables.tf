variable "region" {
  description = "See ADR-0003. Proposed ap-south-1 for latency from Kerala; confirm Bedrock availability first."
  type        = string
  default     = "ap-south-1"
}

variable "profile" {
  description = "Named AWS CLI profile. Created in Lab 01 — do not use the default profile."
  type        = string
  default     = "kaval"
}

variable "alert_email" {
  description = "Budget alerts. You must confirm the SNS subscription email before alerts arrive."
  type        = string
}

variable "monthly_limit_usd" {
  description = "Raised from 25 to 40 on 2026-09-26 by ADR-0008 (database on its own server)."
  type        = number
  default     = 40
}

variable "alert_1_usd" {
  type    = number
  default = 30
}

variable "alert_2_usd" {
  type    = number
  default = 35
}

variable "hard_stop_usd" {
  type    = number
  default = 38
}

variable "ssh_public_key" {
  description = "For ec2-user on the prod node. SSH only works through the SSM tunnel; no port is open. See infra/modules/node."
  type        = string
}
