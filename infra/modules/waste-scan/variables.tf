variable "name_prefix" {
  description = "Names the function, its roles and the parameter, e.g. kaval-staging. Everything after `kaval-` is the Env label the scan treats as this environment's."
  type        = string

  validation {
    condition     = can(regex("^kaval-(prod|staging|dev)$", var.name_prefix))
    error_message = "name_prefix must be kaval-prod, kaval-staging or kaval-dev: the scan matches resources by the Env label this gives."
  }
}

variable "tags" {
  type    = map(string)
  default = {}
}
