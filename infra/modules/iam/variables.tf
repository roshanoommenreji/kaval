variable "name_prefix" {
  description = "e.g. \"kaval-prod\" or \"kaval-staging\". Names the role and instance profile (\"<prefix>-node\"). IAM names are account-global, so two environments can't share one. Required, no default."
  type        = string
}

variable "ecr_repository_arns" {
  description = "Scopes pull access to exactly Kaval's own repos, not every ECR repo in the account."
  type        = list(string)
}

variable "backup_bucket_arn" {
  description = "The node's own role needs this (KAV-32, KAV-74): the nightly dump runs on the app node (a systemd timer, ADR-0038) and reaches the database server over 5432, there being no other compute to run it from."
  type        = string
}

variable "secret_parameter_arns" {
  description = "The DB passwords' and Slack tokens' SSM Parameter Store ARNs (KAV-56). The node's bootstrap script reads these to recreate kaval-postgres-* and kaval-slack on every boot, the same way it already refreshes ecr-cred -- this runs on the trusted host itself, never inside a pod, so it needs no change to IMDS hop limits or pod-level AWS access."
  type        = list(string)
}
