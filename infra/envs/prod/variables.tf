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

variable "app_node_desired_capacity" {
  description = <<-EOT
    1 (default) to run the app node, 0 to pause it. `make down` passes -var=app_node_desired_capacity=0;
    `make up` omits the override, back to the default of 1.

    Found live, KAV-32 Lab 28: `make down` previously ran `terraform destroy -target=module.node`,
    which cascaded into destroying the database (its security group references the node's security
    group) and the entire budget module (the hard-stop Lambda's asg_name used to be a module.node
    output reference). Scaling the ASG to 0 instead costs nothing while paused -- only a *running*
    instance bills -- with none of that blast radius, and matches how this was actually being
    paused by hand before this fix existed.
  EOT
  type        = number
  default     = 1
}
