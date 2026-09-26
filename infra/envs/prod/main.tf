# Kaval — production environment.
#
# Phase 0 provisions ONLY the budget guardrails. The node, network, ECR and IAM
# modules arrive in Phase 4 and are deliberately commented out until then.
#
# Order matters: the thing that stops the bill exists before the thing that
# creates one.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }

  # Local state for Phase 0. Migrating to an S3 backend requires a bucket,
  # which is a resource, which would violate the ordering rule above.
  # Migrate in Phase 4 alongside the rest of the infrastructure.
  # NOTE: terraform.tfstate is gitignored and contains account identifiers.
}

provider "aws" {
  region  = var.region
  profile = var.profile

  default_tags {
    tags = {
      Project   = "kaval"
      ManagedBy = "terraform"
      Env       = "prod"
    }
  }
}

# ─────────────────────────────────────────────────────────────
# Phase 0 — guardrails
# ─────────────────────────────────────────────────────────────

module "budget" {
  source = "../../modules/budget"

  alert_email       = var.alert_email
  monthly_limit_usd = var.monthly_limit_usd
  alert_1_usd       = var.alert_1_usd
  alert_2_usd       = var.alert_2_usd
  hard_stop_usd     = var.hard_stop_usd

  # No compute exists yet, so there is nothing to scale down. The Lambda is
  # still deployed and still fires -- proving the path works before it matters.
  asg_name          = ""
  hard_stop_dry_run = true

  # Armed 2026-09-26 (KAV-30): at the hard-stop threshold ($38 since ADR-0008) the Lambda stops every running Project=kaval
  # instance outside an ASG -- today, the dev server (ADR-0007); from Phase 4 also the
  # database server (ADR-0008). Stop, not terminate.
  stop_tagged_instances = true
}

# ─────────────────────────────────────────────────────────────
# Phase 4 — compute. Do not uncomment before the guardrails have fired
# once in a real test. See docs/labs/lab-01-aws-guardrails.md.
# ─────────────────────────────────────────────────────────────

# module "network" {
#   source = "../../modules/network"
#   region = var.region
#   # No NAT Gateway. Public subnet + security groups. See docs/cost/budget-plan.md.
# }
#
# module "ecr" {
#   source = "../../modules/ecr"
# }
#
# module "iam" {
#   source = "../../modules/iam"
#   # agent  -> read-only
#   # executor -> scoped write
#   # This split is the architecture. See docs/architecture/overview.md.
# }
#
# module "node" {
#   source        = "../../modules/node"
#   subnet_id     = module.network.public_subnet_id
#   instance_type = "t4g.medium"   # Graviton — images MUST be linux/arm64
#   use_spot      = true
# }
