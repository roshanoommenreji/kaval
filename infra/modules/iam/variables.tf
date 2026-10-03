variable "ecr_repository_arns" {
  description = "Scopes pull access to exactly Kaval's own repos, not every ECR repo in the account."
  type        = list(string)
}

variable "backup_bucket_arn" {
  description = "The node's own role needs this (KAV-32): the backup CronJob runs on the app node and reaches the database server over 5432, there being no other compute in the cluster to run it from."
  type        = string
}
