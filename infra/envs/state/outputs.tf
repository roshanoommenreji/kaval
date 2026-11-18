output "bucket_name" {
  description = "Terraform's state bucket. Also in Parameter Store at /kaval/tfstate/bucket."
  value       = aws_s3_bucket.state.id
}

output "plan_role_arn" {
  description = "Goes into the repository secret AWS_PLAN_ROLE_ARN (KAV-89). It holds the account id, so it is a secret and never a file in git."
  value       = module.plan_reader.role_arn
}
