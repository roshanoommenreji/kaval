output "bucket_name" {
  description = "Terraform's state bucket. Also in Parameter Store at /kaval/tfstate/bucket."
  value       = aws_s3_bucket.state.id
}
