# Nightly pg_dump target (ADR-0005/ADR-0008). Its own module, not inlined into infra/envs/prod,
# because staging needs the same pair (bucket + gateway endpoint) later — this is the whole
# module, two resources, exactly reused.

terraform {
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 5.0" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
}

# Bucket names are globally unique across all of AWS, not just this account — the account ID
# would make it unique without a random suffix, but CLAUDE.md forbids ever writing the real
# account ID into a committed file, including implicitly via a Terraform expression whose
# plan/state output could get pasted somewhere. A random suffix sidesteps that entirely.
resource "random_id" "bucket_suffix" {
  byte_length = 4
}

resource "aws_s3_bucket" "backups" {
  bucket = "${var.name_prefix}-db-backups-${random_id.bucket_suffix.hex}"

  tags = { Name = "${var.name_prefix}-db-backups" }
}

resource "aws_s3_bucket_public_access_block" "backups" {
  bucket = aws_s3_bucket.backups.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Abandoned multipart uploads are the one way an S3 bucket's bill creeps up unnoticed —
# nothing here starts one deliberately, but a failed `aws s3 cp` of a large dump could.
resource "aws_s3_bucket_lifecycle_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id

  rule {
    id     = "abort-incomplete-multipart"
    status = "Enabled"
    filter {} # applies to every object; required explicitly since provider 5.x
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

# Free: a route in the VPC's route table, not a metered interface endpoint. backup.sh's
# `aws s3 cp` from inside the cluster never crosses the public internet or costs a
# per-GB data-transfer charge.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = var.vpc_id
  service_name      = "com.amazonaws.${data.aws_region.current.name}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = var.route_table_ids

  tags = { Name = "${var.name_prefix}-s3-backups" }
}

data "aws_region" "current" {}
