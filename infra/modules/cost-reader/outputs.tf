output "role_arn" {
  description = "The role the server's cost pull borrows (the job builds the ARN itself from the account and name_prefix, so nothing reads this at run time; it is here for the lab's checks)."
  value       = aws_iam_role.reader.arn
}

output "role_name" {
  value = aws_iam_role.reader.name
}
