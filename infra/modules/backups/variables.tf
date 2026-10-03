variable "name_prefix" {
  description = "e.g. \"kaval-prod\". Also the bucket name prefix — bucket names are globally unique, so this alone isn't enough; see main.tf's random suffix."
  type        = string
}

variable "vpc_id" {
  type = string
}

variable "route_table_ids" {
  description = "Route tables that should get the S3 gateway endpoint's route (free, no NAT needed)."
  type        = list(string)
}
