variable "region" {
  type    = string
  default = "ap-south-1"
}

variable "profile" {
  type    = string
  default = "kaval"
}

variable "ssh_public_key" {
  description = "Contents of ~/.ssh/kaval-devbox.pub. Set in terraform.tfvars (gitignored)."
  type        = string
}
