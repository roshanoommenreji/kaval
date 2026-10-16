variable "region" {
  description = "See ADR-0003."
  type        = string
  default     = "ap-south-1"
}

variable "profile" {
  description = "Named AWS CLI profile (Lab 01)."
  type        = string
  default     = "kaval"
}
