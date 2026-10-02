variable "vpc_id" {
  type = string
}

variable "subnet_ids" {
  description = "One per AZ (KAV-51) — the ASG's vpc_zone_identifier, so it can launch the node in whichever AZ has spot capacity right now instead of a single pinned one."
  type        = list(string)
}

variable "instance_profile_name" {
  type = string
}

variable "instance_type" {
  description = "Must be Graviton (arm64) — see CLAUDE.md constraint #2."
  type        = string
  default     = "t4g.medium"
}

variable "disk_gb" {
  type    = number
  default = 20
}

variable "ssh_public_key" {
  description = "For ec2-user, reachable only through the SSM tunnel — same pattern as the dev server, no inbound SSH port."
  type        = string
}

variable "k3s_version" {
  description = "Pinned, checksum-verified. Matches the kubectl version already pinned in infra/modules/devbox/user_data.sh.tftpl."
  type        = string
  default     = "v1.37.1+k3s1"
}

variable "k3s_sha256_arm64" {
  description = "From https://github.com/k3s-io/k3s/releases/download/<version>/sha256sum-arm64.txt, verified at the time this version was pinned."
  type        = string
  default     = "a1561ca4aef8b99f5588a840d4468ac39c8c1cd92470bbcb9634823fa221741a"
}

variable "flux_version" {
  description = "Pinned, checksum-verified (KAV-51). Installs the GitOps controllers only — not `flux bootstrap`, which would commit back to the repo and bypass the PR-required branch protection (CLAUDE.md)."
  type        = string
  default     = "2.9.6"
}

variable "flux_sha256_arm64" {
  description = "From https://github.com/fluxcd/flux2/releases/download/v<version>/flux_<version>_checksums.txt, verified at the time this version was pinned."
  type        = string
  default     = "6663c154755b732f43dc993d72321f71c2fc1ff0bcb94b2696e0a8638fa562b4"
}
