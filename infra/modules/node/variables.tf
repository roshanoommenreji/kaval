variable "name_prefix" {
  description = "\"kaval-prod\" or \"kaval-staging\". Names the ASG, launch template, security group and instance, and sets the Kubernetes namespace and SSM parameter path the bootstrap script uses. Must start with \"kaval-\": the rest picks the deploy/gitops/<env> directory Flux is pointed at. Required, no default."
  type        = string

  validation {
    condition     = startswith(var.name_prefix, "kaval-")
    error_message = "name_prefix must start with \"kaval-\" (e.g. kaval-prod, kaval-staging): the remainder selects deploy/gitops/<env>."
  }
}

variable "backup_bucket_name" {
  description = "The S3 bucket the node dumps its environment's database into every night (KAV-74, ADR-0038), via a systemd timer on the node using its own instance role. Empty (the default) installs no timer. The node's role must be able to write to this bucket (infra/modules/iam backup_bucket_arn)."
  type        = string
  default     = ""
}

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

variable "desired_capacity" {
  description = "0 to pause (ASG and its launch template/security group stay, nothing running -- no compute or IPv4 charge), 1 to run. min_size tracks this so the ASG doesn't fight to relaunch while paused; max_size stays fixed at 1 -- this node never autoscales beyond one instance."
  type        = number
  default     = 1
}

variable "spot" {
  description = "On-Demand by default (ADR-0028): ~$0.0224/hr, no reclamation, and it launches when asked. Spot (true) is ~$0.0105/hr but had no t4g.medium capacity in any AZ on 2026-10-06 and 2026-10-08 (KAV-32 Lab 28, KAV-57 Lab 29). Set true to trade that reliability for ~$8.55/month always-on."
  type        = bool
  default     = false
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
