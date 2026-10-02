variable "ecr_repository_arns" {
  description = "Scopes pull access to exactly Kaval's own repos, not every ECR repo in the account."
  type        = list(string)
}
