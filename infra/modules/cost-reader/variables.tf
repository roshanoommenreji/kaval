variable "name_prefix" {
  description = "Names the role, e.g. kaval-prod. The server's job builds the same name, so this must match the node's name_prefix."
  type        = string
}

variable "node_role_arn" {
  description = "The server's own role (module.iam.node_role_arn): the only principal allowed to borrow the cost reader."
  type        = string
}

variable "node_role_name" {
  description = "The same role's name (module.iam.node_role_name): it is given the right to borrow the cost reader."
  type        = string
}

variable "tags" {
  type    = map(string)
  default = {}
}
