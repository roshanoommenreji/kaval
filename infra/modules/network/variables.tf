variable "name_prefix" {
  description = "e.g. \"kaval-prod\" or \"kaval-staging\". Names the VPC, internet gateway, subnets and route table. Required, no default, so a new call site can't silently collide with prod (same pattern as infra/modules/database and backups)."
  type        = string
}

variable "vpc_cidr" {
  description = <<-EOT
    A dedicated VPC per environment (the defaults below are prod's; staging overrides them), distinct from the default VPC the dev server uses. NOT
    10.42.0.0/16 (KAV-51, found live): that's k3s's own default pod-network CIDR, and
    using it for the VPC too meant the VPC's real DNS resolver (base+2, so 10.42.0.2) sat
    inside the range Flannel's overlay claims for pods — traffic to it got captured by the
    overlay instead of reaching the resolver, so CoreDNS's upstream forward failed with
    "connection refused"/timeout and nothing in the cluster could resolve an external name,
    including Flux's own GitRepository clone. 10.43.0.0/16 (k3s's default service CIDR) is
    the other one to avoid.
  EOT
  type        = string
  default     = "10.60.0.0/16"
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
  default     = ["10.60.1.0/24", "10.60.2.0/24", "10.60.3.0/24"]
}
