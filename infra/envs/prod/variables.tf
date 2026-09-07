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
  type    = number
  default = 25
}

variable "alert_1_usd" {
  type    = number
  default = 18
}

variable "alert_2_usd" {
  type    = number
  default = 22
}

variable "hard_stop_usd" {
  type    = number
  default = 24
}
