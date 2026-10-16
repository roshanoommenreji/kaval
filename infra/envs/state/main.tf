# Kaval -- the home of Terraform's own record (KAV-86, ADR-0058).
#
# One private S3 bucket holds the state of every other environment (prod, staging, dev), one
# object each. It is a folder of its own, not part of prod's or staging's, for one reason:
# `make staging-down` destroys everything in staging's state, and this bucket must never be
# inside anything that gets destroyed.
#
# Bootstrap order: the first `apply` runs with local state (the bucket cannot hold the record
# of its own creation); then the block below is switched on and `terraform init -migrate-state`
# moves this folder's own record into the bucket, like every other environment's.
#
# The state holds generated database passwords and Slack tokens, so the bucket is private,
# encrypted, TLS-only and versioned. Locking is S3's own (`use_lockfile`, Terraform 1.10+):
# no lock table to pay for or keep alive. Cost: a few MB of objects, well under $0.01 a month.

terraform {
  required_version = ">= 1.11"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 5.0" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }

  # Switched on after the first apply (Lab 55). Partial configuration: the bucket name is not
  # written here (public repo); `scripts/ops/tf-init.sh state` supplies it from Parameter Store.
  backend "s3" {
    key          = "state/terraform.tfstate"
    region       = "ap-south-1"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region  = var.region
  profile = var.profile

  default_tags {
    tags = {
      Project   = "kaval"
      ManagedBy = "terraform"
      Env       = "state"
    }
  }
}

# Globally unique bucket names; a random suffix keeps the account ID out of every file.
resource "random_id" "bucket_suffix" {
  byte_length = 4
}

resource "aws_s3_bucket" "state" {
  bucket = "kaval-tfstate-${random_id.bucket_suffix.hex}"

  # Losing this bucket loses the record of everything built. Versioning keeps history; this
  # keeps the bucket itself (a destroy must first be edited out of the code, in review).
  force_destroy = false
  lifecycle {
    prevent_destroy = true
  }

  tags = { Name = "kaval-tfstate" }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket = aws_s3_bucket.state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# SSE-S3 (free). A customer key would add $1/month and a second thing to lose.
resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Old versions are the undo history; keep 90 days of them, at least the newest 20 whatever
# their age. Current objects are never expired.
resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    id     = "expire-old-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days           = 90
      newer_noncurrent_versions = 20
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  depends_on = [aws_s3_bucket_versioning.state]
}

data "aws_iam_policy_document" "state" {
  statement {
    sid       = "TlsOnly"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket     = aws_s3_bucket.state.id
  policy     = data.aws_iam_policy_document.state.json
  depends_on = [aws_s3_bucket_public_access_block.state]
}

# The bucket's name is not in git. This parameter is how a new laptop finds it
# (scripts/ops/tf-init.sh). Free tier, not a secret.
resource "aws_ssm_parameter" "bucket" {
  name  = "/kaval/tfstate/bucket"
  type  = "String"
  value = aws_s3_bucket.state.id

  tags = { Name = "kaval-tfstate-bucket" }
}
