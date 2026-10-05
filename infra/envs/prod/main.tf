# Kaval — production environment.
#
# Phase 0 provisioned the budget guardrails. Phase 4 (KAV-50) added the network, ECR, IAM
# and the node itself. KAV-51 added Flux GitOps. KAV-32 adds the database server and its
# S3 backup target — Postgres moves off the app node for good (ADR-0008).
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

  # KAV-50: the ASG now exists, so the hard-stop Lambda has something real to scale to
  # zero. Armed (not dry-run) from the moment compute exists -- the whole point of
  # building the guardrail in Phase 0 was to have it live before anything could overrun.
  asg_name          = module.node.asg_name
  hard_stop_dry_run = false

  # Armed 2026-09-26 (KAV-30): at the hard-stop threshold ($38 since ADR-0008) the Lambda stops every running Project=kaval
  # instance outside an ASG -- today, the dev server (ADR-0007); from Phase 4 also the
  # database server (ADR-0008). Stop, not terminate.
  stop_tagged_instances = true

  # KAV-32, 2026-10-05: the third stop path ADR-0008 names, alongside `make down` and the hard
  # stop above -- a 02:00 IST EventBridge Scheduler rule that invokes this same Lambda as a brake
  # for a forgotten `make down`. True for the paused posture (Phases 4-6); flip to false when
  # Phase 7 makes the system always-on.
  nightly_auto_stop_enabled = true
}

# ─────────────────────────────────────────────────────────────
# Phase 4 — compute (KAV-50). The node, not yet what runs on it: Flux GitOps, the
# Cloudflare Tunnel and the database server (KAV-32, ADR-0008) are follow-on stories.
# ─────────────────────────────────────────────────────────────

module "network" {
  source = "../../modules/network"
}

module "ecr" {
  source = "../../modules/ecr"
}

module "iam" {
  source = "../../modules/iam"

  # The agent/executor read-only vs scoped-write split is already enforced at the
  # Kubernetes RBAC layer (ADR-0021, KAV-47). This is the node's own AWS identity --
  # SSM management and ECR pulls -- not a per-service AWS role. See infra/modules/iam.
  ecr_repository_arns = module.ecr.repository_arns
  backup_bucket_arn   = module.backups.bucket_arn

  # KAV-56: the node's bootstrap script reads these to recreate kaval-postgres-* and
  # kaval-slack on every boot -- see the "automatic Secret recreation" section below.
  secret_parameter_arns = concat(
    module.database.ssm_parameter_arns,
    [
      aws_ssm_parameter.slack_bot_token.arn,
      aws_ssm_parameter.slack_app_token.arn,
      aws_ssm_parameter.slack_channel_id.arn,
    ]
  )
}

module "node" {
  source = "../../modules/node"

  vpc_id                = module.network.vpc_id
  subnet_ids            = module.network.public_subnet_ids
  instance_profile_name = module.iam.instance_profile_name
  ssh_public_key        = var.ssh_public_key
  # instance_type, disk_gb, k3s_version, k3s_sha256_arm64 keep their module defaults.
  # database_private_ip is NOT wired here: module.database already takes this node's own
  # security group ID as an input, so a Terraform-time dependency the other way round would
  # cycle. The bootstrap script looks the database instance up by its Role=database tag at
  # boot instead (KAV-56) -- also more robust if the DB server is ever replaced.
}

# ─────────────────────────────────────────────────────────────
# Phase 4 — the database server (KAV-32, ADR-0008). Its own on-demand t4g.small, its own
# security group, its own data volume -- not a pod on the node above. See
# infra/modules/database for why, and docs/adr/0008's amendment for the implementation
# decisions (TLS, per-service roles, the AZ pinned here rather than following the node's
# spot capacity).
# ─────────────────────────────────────────────────────────────

module "backups" {
  source = "../../modules/backups"

  name_prefix     = "kaval-prod"
  vpc_id          = module.network.vpc_id
  route_table_ids = [module.network.public_route_table_id]
}

module "database" {
  source = "../../modules/database"

  name_prefix                = "kaval-prod"
  vpc_id                     = module.network.vpc_id
  subnet_id                  = module.network.public_subnet_ids[0] # pinned AZ -- see ADR-0008's amendment
  app_node_security_group_id = module.node.security_group_id
  ssh_public_key             = var.ssh_public_key
}

# ─────────────────────────────────────────────────────────────
# Phase 4 — automatic Secret recreation (KAV-56). A node replacement (spot reclamation,
# seen live 2026-10-04) wipes every Secret the cluster was holding, because k3s's own
# state lives only on that one node. The node's own bootstrap script (same pattern as its
# existing ecr-cred refresh, infra/modules/node/user_data.sh.tftpl) now also recreates the
# five kaval-postgres-* Secrets and kaval-slack from here on every boot and every 6h after
# -- no one has to run `kubectl create secret` by hand again.
#
# These three hold placeholder values on purpose: Terraform can generate a database
# password itself (random_password, see infra/modules/database), but a Slack bot/app
# token comes from Slack's own app configuration -- there's nothing for Terraform to
# generate. Roshan sets the real values once, directly, with
# `aws ssm put-parameter --overwrite` -- never pasted to an assistant, same discipline as
# every other credential here. `ignore_changes` keeps Terraform from fighting that real
# value on every future plan.
# ─────────────────────────────────────────────────────────────

resource "aws_ssm_parameter" "slack_bot_token" {
  name  = "/kaval/kaval-prod/slack/bot-token"
  type  = "SecureString"
  value = "REPLACE_ME"

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_ssm_parameter" "slack_app_token" {
  name  = "/kaval/kaval-prod/slack/app-token"
  type  = "SecureString"
  value = "REPLACE_ME"

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_ssm_parameter" "slack_channel_id" {
  name  = "/kaval/kaval-prod/slack/channel-id"
  type  = "String" # not sensitive, kept alongside the other two so one script reads all three as a unit
  value = "REPLACE_ME"

  lifecycle {
    ignore_changes = [value]
  }
}
