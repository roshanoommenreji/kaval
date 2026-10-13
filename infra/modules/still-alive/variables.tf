variable "name_prefix" {
  description = "Names the role, e.g. kaval."
  type        = string
}

variable "github_repo_claim" {
  description = "The repository exactly as GitHub writes it in the token's sub claim (immutable form with numeric ids); see modules/ci-publish for how to read it."
  type        = string
}

variable "branch" {
  description = "The only branch whose jobs (scheduled runs included) may assume the role."
  type        = string
  default     = "main"
}

variable "tags" {
  type    = map(string)
  default = {}
}
