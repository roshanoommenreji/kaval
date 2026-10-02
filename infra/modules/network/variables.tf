variable "vpc_cidr" {
  description = "A dedicated VPC for prod, distinct from the default VPC the dev server uses."
  type        = string
  default     = "10.42.0.0/16"
}

variable "public_subnet_cidr" {
  type    = string
  default = "10.42.1.0/24"
}

variable "availability_zone" {
  description = "Single-AZ — the node is one instance, so a second AZ buys nothing without a second node. ap-south-1b, not -1a: t4g.medium spot capacity in -1a was exhausted at apply time (2026-10-02), and AWS's own error pointed at -1b/-1c."
  type        = string
  default     = "ap-south-1b"
}
