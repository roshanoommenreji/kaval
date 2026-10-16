# Kaval — development environment: one on-demand server the Phase 1–3 stack runs on,
# instead of the laptop (ADR-0007). This is where ADR-0004's `local` tier now runs; the
# promotion path local -> staging -> prod is unchanged.
#
# Cost (ap-south-1, verified 2026-09-26): t4g.medium $0.0224/hr while running,
# 30 GB gp3 ~$2.74/mo always, public IPv4 $0.005/hr while running. See docs/cost/budget-plan.md.

terraform {
  required_version = ">= 1.11"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }

  # Remote state (KAV-86, ADR-0058). The bucket name is not in git: scripts/ops/tf-init.sh dev
  # reads it from Parameter Store. Locking is S3's own.
  backend "s3" {
    key          = "dev/terraform.tfstate"
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
      Env       = "dev"
    }
  }
}

module "devbox" {
  source         = "../../modules/devbox"
  ssh_public_key = var.ssh_public_key
}
