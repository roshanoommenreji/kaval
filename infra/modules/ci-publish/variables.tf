variable "name_prefix" {
  description = "Names the role, e.g. kaval."
  type        = string
}

variable "github_repo" {
  description = "owner/name of the only repository allowed to assume the role. Documentation and tagging only; the trust check uses github_repo_claim."
  type        = string
}

variable "github_repo_claim" {
  description = "The repository exactly as GitHub writes it in the token's sub claim. This repo uses immutable subjects (the default for new repositories), which carry numeric ids: owner@<owner id>/repo@<repo id>. Read it with `gh api repos/<owner>/<repo>/actions/oidc/customization/sub` (sub_claim_prefix, without the leading repo:). The ids cannot be taken over if the name is later released and re-registered, which is why the immutable form is stricter than a plain owner/name."
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
