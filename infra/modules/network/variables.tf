variable "vpc_cidr" {
  description = "A dedicated VPC for prod, distinct from the default VPC the dev server uses."
  type        = string
  default     = "10.42.0.0/16"
}

variable "availability_zones" {
  description = <<-EOT
    One public subnet per AZ (KAV-51). Started single-AZ, pinned to whichever one AWS's
    own error pointed at — but that pin failed twice in one day (ap-south-1a exhausted at
    KAV-50's apply on 2026-10-02, then ap-south-1b exhausted at KAV-51's), which is spot
    capacity being genuinely volatile, not a one-off. A single node still only ever runs
    in one AZ at a time; this just lets the ASG's own launch choose whichever of the three
    has capacity right now, instead of a human re-pinning one AZ after every failure.
  EOT
  type        = list(string)
  default     = ["ap-south-1a", "ap-south-1b", "ap-south-1c"]
}

variable "public_subnet_cidrs" {
  description = "One /24 per AZ, in the same order as availability_zones."
  type        = list(string)
  default     = ["10.42.1.0/24", "10.42.2.0/24", "10.42.3.0/24"]
}
