variable "name_prefix" {
  description = "Names the role, e.g. kaval."
  type        = string
}

variable "github_repo_claim" {
  description = "The repository exactly as GitHub writes it in the token's sub claim (immutable form with numeric ids); see modules/ci-publish for how to read it."
  type        = string
}

variable "state_bucket_arn" {
  description = "Terraform's state bucket. The role may list it and read the objects named in state_keys."
  type        = string
}

variable "state_keys" {
  description = "The state objects a pull request plan reads (one per environment it plans)."
  type        = list(string)
}

variable "tags" {
  type    = map(string)
  default = {}
}
