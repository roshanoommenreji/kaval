# Kaval — the EKS lab (Phase 7, KAV-101, ADR-0067). A portability chapter, NOT a promotion tier
# (CLAUDE.md): nothing is promoted through it and nothing depends on it. It proves that the chart
# production runs also runs on real EKS with a different values file and no template edit, and
# exercises the parts k3s cannot teach (IRSA, the load balancer controller, a managed control plane).
#
# EPHEMERAL. The control plane alone is $0.10 an hour (about $73 a month). Create it for a session,
# destroy it the same day; `make lab-up` / `make lab-down`, runbook docs/runbooks/lab-eks.md. If a
# person forgets, module "reaper" scales the servers to zero at 4 hours and deletes the cluster at
# 6, and the plan is always read before an apply.
#
# What is deliberately NOT here:
#   - module "ecr": the images are the ones everything else uses (infra/envs/prod made the repos).
#   - module "budget": the account-wide guardrail already lives in prod's state.
#   - a database server: the lab runs the chart's own in-cluster Postgres on an EBS disk.
#   - a NAT gateway or a persistent load balancer ($32 and $18 a month, CLAUDE.md). The one load
#     balancer the lab makes is made by the controller during the session and removed before destroy.

terraform {
  required_version = ">= 1.11"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }

  # Remote state (KAV-86, ADR-0058). The bucket name is not in git: scripts/ops/tf-init.sh lab-eks
  # reads it from Parameter Store. Locking is S3's own.
  backend "s3" {
    key          = "lab-eks/terraform.tfstate"
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
      Env       = "lab-eks"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  name = "kaval-lab-eks"

  # The same repositories infra/modules/ecr creates for prod. ARNs are deterministic.
  ecr_repository_arns = [
    for name in ["gateway", "agent", "executor", "collector", "backup"] :
    "arn:aws:ecr:${var.region}:${data.aws_caller_identity.current.account_id}:repository/kaval/${name}"
  ]
}

module "network" {
  source = "../../modules/network"

  name_prefix = local.name
  # Prod is 10.60.0.0/16 and staging 10.61.0.0/16; the lab is 10.62.0.0/16. Distinct, so any two
  # could be peered one day. The pod network on EKS is the VPC's own, so there is no overlay range
  # to avoid (unlike k3s, see the network module's vpc_cidr note).
  vpc_cidr            = "10.62.0.0/16"
  public_subnet_cidrs = ["10.62.1.0/24", "10.62.2.0/24", "10.62.3.0/24"]
}

module "cluster" {
  source = "../../modules/eks-lab"

  name                = local.name
  kubernetes_version  = var.kubernetes_version
  vpc_id              = module.network.vpc_id
  subnet_ids          = module.network.public_subnet_ids
  api_allowed_cidrs   = var.api_allowed_cidrs
  node_count          = var.node_count
  ecr_repository_arns = local.ecr_repository_arns
}

# The self-destruct timer. Lives in this state on purpose: if the lab is destroyed normally, so is
# the timer; if it is not, the timer finishes the job.
module "reaper" {
  source = "../../modules/lab-reaper"

  name_prefix     = local.name
  cluster_name    = module.cluster.cluster_name
  scale_in_hours  = var.scale_in_hours
  delete_hours    = var.delete_hours
  dry_run         = var.reaper_dry_run
  alarm_topic_arn = var.alarm_topic_arn
}
