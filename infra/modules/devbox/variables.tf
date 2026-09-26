variable "instance_type" {
  description = "t4g.medium matches the planned prod node, so measurements carry over. Must be Graviton (arm64)."
  type        = string
  default     = "t4g.medium"
}

variable "availability_zone" {
  type    = string
  default = "ap-south-1a"
}

variable "disk_gb" {
  description = "Room for Docker images and several model downloads (~2 GB each). gp3 bills even while stopped."
  type        = number
  default     = 30
}

variable "idle_minutes" {
  description = "Stop the instance after this many minutes with no SSH or SSM session."
  type        = number
  default     = 60
}

variable "ssh_public_key" {
  description = "Public key for ec2-user. SSH only works through the SSM tunnel; no port is open."
  type        = string
}
