# Kaval — staging environment (KAV-57, ADR-0004).
#
# A genuine second cluster, not namespaces on the prod node: its own VPC, its own spot node
# running its own k3s and Flux, its own database server. Created for a release and destroyed
# after, so it costs pennies rather than ~$25/month always-on.
#
# Same modules as infra/envs/prod, different name_prefix. What is deliberately NOT here:
#   - module "ecr": images live in one shared set of repos (ADR-0004: build once, promote the
#     artifact), created by infra/envs/prod. A second module call would try to create
#     repositories that already exist. Staging only needs permission to pull them.
#   - module "budget": the account-wide guardrail already lives in prod's state, and its hard
#     stop stops every running Project=kaval instance, staging's included.
#   - the nightly auto-stop and idle self-destruct: separate ROADMAP lines. Until they exist
#     staging is destroyed by hand (docs/labs/lab-29-staging-environment.md).

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }

  # Local state, same as prod. terraform.tfstate is gitignored and holds account identifiers.
}

provider "aws" {
  region  = var.region
  profile = var.profile

  default_tags {
    tags = {
      Project   = "kaval"
      ManagedBy = "terraform"
      Env       = "staging"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  name_prefix = "kaval-staging"

  # The same four repositories infra/modules/ecr creates for prod (its repository_names
  # default). ARNs are deterministic, so there is no need to read prod's state.
  ecr_repository_arns = [
    for name in ["gateway", "agent", "executor", "collector"] :
    "arn:aws:ecr:${var.region}:${data.aws_caller_identity.current.account_id}:repository/kaval/${name}"
  ]
}

module "network" {
  source = "../../modules/network"

  name_prefix = local.name_prefix
  # Prod is 10.60.0.0/16. Distinct ranges mean the two VPCs could be peered one day without
  # renumbering. Still clear of k3s's own 10.42.0.0/16 pod and 10.43.0.0/16 service ranges
  # (see the network module's vpc_cidr note).
  vpc_cidr            = "10.61.0.0/16"
  public_subnet_cidrs = ["10.61.1.0/24", "10.61.2.0/24", "10.61.3.0/24"]
}

module "backups" {
  source = "../../modules/backups"

  name_prefix     = local.name_prefix
  vpc_id          = module.network.vpc_id
  route_table_ids = [module.network.public_route_table_id]
}

# Staging has no Slack: both environments sharing one Socket Mode app would split events
# between them. The node's bootstrap script reads these three on every boot though, and aborts
# if one is missing, so they exist with placeholder values, and the chart's slackSecretName is
# left unset in deploy/environments/staging/values.yaml so nothing consumes them.
resource "aws_ssm_parameter" "slack" {
  for_each = {
    bot-token  = "SecureString"
    app-token  = "SecureString"
    channel-id = "String"
  }

  name  = "/kaval/${local.name_prefix}/slack/${each.key}"
  type  = each.value
  value = "REPLACE_ME"

  lifecycle {
    ignore_changes = [value]
  }
}

module "iam" {
  source = "../../modules/iam"

  name_prefix         = local.name_prefix
  ecr_repository_arns = local.ecr_repository_arns
  backup_bucket_arn   = module.backups.bucket_arn
  secret_parameter_arns = concat(
    module.database.ssm_parameter_arns,
    [for p in aws_ssm_parameter.slack : p.arn],
  )
}

module "node" {
  source = "../../modules/node"

  name_prefix           = local.name_prefix
  vpc_id                = module.network.vpc_id
  subnet_ids            = module.network.public_subnet_ids
  instance_profile_name = module.iam.instance_profile_name
  ssh_public_key        = var.ssh_public_key
  desired_capacity      = var.node_desired_capacity
  spot                  = var.node_spot
}

module "database" {
  source = "../../modules/database"

  name_prefix                = local.name_prefix
  vpc_id                     = module.network.vpc_id
  subnet_id                  = module.network.public_subnet_ids[0]
  app_node_security_group_id = module.node.security_group_id
  ssh_public_key             = var.ssh_public_key

  # Created per release and destroyed after, so `terraform destroy` has to be able to
  # terminate it. The data volume keeps its prevent_destroy: see the database module.
  termination_protection = false
  data_disk_gb           = 10 # ROADMAP: staging's 10 GB EBS
}
