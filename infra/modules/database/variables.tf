variable "name_prefix" {
  description = "e.g. \"kaval-prod\". Used for resource names and the SSM Parameter Store path prefix (/kaval/<env>/db/...), so staging can reuse this module without colliding with prod's parameters."
  type        = string
}

variable "vpc_id" {
  type = string
}

variable "subnet_id" {
  description = "A single subnet (one AZ), unlike the app node's list — the database must not move AZ on its own. See ADR-0008's amendment for the cross-AZ trade-off this accepts."
  type        = string
}

variable "app_node_security_group_id" {
  description = "The only thing allowed to reach port 5432 (ADR-0008)."
  type        = string
}

variable "instance_type" {
  description = "On-demand, never spot — a database must not be reclaimable at two minutes' notice (ADR-0008)."
  type        = string
  default     = "t4g.small"
}

variable "root_disk_gb" {
  description = "OS + Docker images only. The actual data lives on its own volume (data_disk_gb)."
  type        = number
  default     = 8
}

variable "data_disk_gb" {
  type    = number
  default = 20
}

variable "db_name" {
  type    = string
  default = "kaval"
}

variable "ssh_public_key" {
  description = "For ec2-user, reachable only through the SSM tunnel — same no-inbound-port posture as infra/modules/devbox and infra/modules/node."
  type        = string
}
