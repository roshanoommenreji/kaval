variable "name_prefix" {
  description = "Names the function, its roles and the parameters, e.g. kaval-staging. Everything after `kaval-` is the Env label the worker will act on, and no other."
  type        = string

  validation {
    condition     = can(regex("^kaval-(prod|staging|dev)$", var.name_prefix))
    error_message = "name_prefix must be kaval-prod, kaval-staging or kaval-dev: the worker acts only where the Env label this gives matches."
  }
}

variable "grace_hours" {
  description = "How long a disk stays marked before it may be deleted. 168 (7 days) in production; staging uses 1 so the live proof is quick (ADR-0063 decision 1)."
  type        = number
  default     = 168

  validation {
    condition     = var.grace_hours >= 1
    error_message = "grace_hours must be at least 1: a grace period of nothing is not a grace period."
  }
}

variable "enabled" {
  description = "The one switch. false makes the worker refuse every order with the word `disabled` (the role and parameters stay)."
  type        = bool
  default     = true
}

variable "tags" {
  type    = map(string)
  default = {}
}
