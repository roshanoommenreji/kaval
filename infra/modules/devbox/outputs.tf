output "instance_id" {
  description = "Stable across stop/start. Used by make devbox-* and the SSH config."
  value       = aws_instance.devbox.id
}

output "availability_zone" {
  value = aws_instance.devbox.availability_zone
}
