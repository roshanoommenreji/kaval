variable "name_prefix" {
  description = "Names the role, e.g. kaval."
  type        = string
}

variable "github_repo" {
  description = "owner/name of the only repository allowed to assume the role."
  type        = string
}

variable "branch" {
  description = "The only branch whose jobs may assume the role."
  type        = string
  default     = "main"
}

variable "ecr_repository_arns" {
  description = "The repositories the role may push to (module.ecr.repository_arns)."
  type        = list(string)
}

variable "tags" {
  type    = map(string)
  default = {}
}
