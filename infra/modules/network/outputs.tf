output "vpc_id" {
  value = aws_vpc.this.id
}

output "public_subnet_ids" {
  description = "One per AZ, in the order availability_zones declares them — fed into the node module's ASG so it can launch in whichever has spot capacity (KAV-51)."
  value       = [for az in var.availability_zones : aws_subnet.public[az].id]
}
