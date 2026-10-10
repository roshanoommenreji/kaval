variable "name_prefix" {
  description = "e.g. \"kaval-prod\". Also the bucket name prefix — bucket names are globally unique, so this alone isn't enough; see main.tf's random suffix."
  type        = string
}

variable "force_destroy" {
  description = "Let `terraform destroy` delete the bucket even if it holds files. Staging only (KAV-73): it is destroyed after every release and now holds the seed and its own dumps, so a plain destroy would stop on 'BucketNotEmpty'. Prod keeps false: its dumps are the recovery point and must never be deleted by a destroy."
  type        = bool
  default     = false
}

variable "vpc_id" {
  type = string
}

variable "route_table_ids" {
  description = "Route tables that should get the S3 gateway endpoint's route (free, no NAT needed)."
  type        = list(string)
}
