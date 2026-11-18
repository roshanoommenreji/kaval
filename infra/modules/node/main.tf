# One environment's node (prod or staging, by var.name_prefix): one t4g.medium (c6g.large when AWS has no t4g.medium, KAV-163) in an ASG pinned to size 1, running k3s. A launch
# template + ASG rather than a bare instance (unlike infra/modules/devbox) because spot
# capacity can be reclaimed with two minutes' notice — the ASG's job is to replace it
# without anyone paged. See docs/cost/budget-plan.md for the spot math.
#
# A replacement instance now gets the Helm release back on its own: cloud-init installs
# Flux and bootstraps it against this repo (KAV-51, ADR-0025), so a reclamation today
# means a fresh k3s that reconciles itself to the last commit on `main` within Flux's own
# poll interval — the self-healing property the Phase 4 exit gate tests. See
# docs/labs/lab-19-aws-landing.md and docs/labs/lab-20-flux-gitops.md.

terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

data "aws_ssm_parameter" "al2023_arm64" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

resource "aws_security_group" "node" {
  name        = "${var.name_prefix}-node"
  description = "No inbound rules. Access is through SSM Session Manager only."
  vpc_id      = var.vpc_id

  egress {
    description      = "Image pulls, package installs, SSM"
    from_port        = 0
    to_port          = 0
    protocol         = "-1"
    cidr_blocks      = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }

  tags = { Name = "${var.name_prefix}-node" }
}

locals {
  # ADR-0061: every Kaval resource carries these three. Env is the part of the name after "kaval-".
  required_tags = {
    Project   = "kaval"
    Env       = trimprefix(var.name_prefix, "kaval-")
    ManagedBy = "terraform"
  }
}

resource "aws_launch_template" "node" {
  name_prefix = "${var.name_prefix}-"
  image_id    = data.aws_ssm_parameter.al2023_arm64.insecure_value
  # The first size; the group's own list below decides which size really launches (KAV-163).
  instance_type = var.instance_types[0]

  iam_instance_profile {
    name = var.instance_profile_name
  }

  vpc_security_group_ids = [aws_security_group.node.id]

  metadata_options {
    http_tokens   = "required" # IMDSv2 only
    http_endpoint = "enabled"
  }

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_type           = "gp3"
      volume_size           = var.disk_gb
      encrypted             = true
      delete_on_termination = true
    }
  }

  # base64gzip, not base64encode: EC2 refuses user data over 16384 bytes, and this script
  # outgrew that with the nightly-dump timer (KAV-74, ADR-0038; first seen live on the first
  # `make up` after it, "InvalidUserData.Malformed"). cloud-init unzips gzip user data by itself.
  # Zipped it is ~6.6 KB, so there is room; `terraform plan` does NOT check this limit.
  user_data = base64gzip(templatefile("${path.module}/user_data.sh.tftpl", {
    ssh_public_key        = var.ssh_public_key
    k3s_version           = var.k3s_version
    k3s_sha256_arm64      = var.k3s_sha256_arm64
    flux_version          = var.flux_version
    flux_sha256_arm64     = var.flux_sha256_arm64
    name_prefix           = var.name_prefix
    backup_bucket_name    = var.backup_bucket_name
    seed_database         = var.seed_database
    image_checker_enabled = var.image_checker_enabled
    gitops_env            = trimprefix(var.name_prefix, "kaval-") # "prod" / "staging": the deploy/gitops/<this> directory
  }))

  # The three required tags (ADR-0061) have to be written here because Terraform's provider-wide
  # default tags do not reach what an Auto Scaling group launches. Found 2026-10-18: the live
  # staging server carried only Name and Project, and its disk carried nothing.
  tag_specifications {
    resource_type = "instance"
    tags          = merge(local.required_tags, { Name = var.name_prefix })
  }

  tag_specifications {
    resource_type = "volume"
    tags          = merge(local.required_tags, { Name = "${var.name_prefix}-root" })
  }

  # A newer AMI or an edited script must not silently replace a running node mid-release.
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_autoscaling_group" "node" {
  name                = var.name_prefix
  min_size            = var.desired_capacity # tracks desired_capacity 1:1 -- paused means 0, not "should relaunch"
  max_size            = 1                    # never autoscales beyond one instance, paused or not
  desired_capacity    = var.desired_capacity
  vpc_zone_identifier = var.subnet_ids
  health_check_type   = "EC2" # no load balancer target to check against

  # The group's desired size, once a minute, so the still-alive alarm (infra/modules/still-alive)
  # can tell "parked on purpose" (wants 0) from "should be running". Free: group metrics at
  # one-minute granularity are not charged.
  enabled_metrics     = ["GroupDesiredCapacity"]
  metrics_granularity = "1Minute"

  # A prioritised list of equivalent sizes (KAV-163, ADR-0103): the group tries var.instance_types in
  # order and falls back by itself when AWS has none of the first. "prioritized" is what keeps
  # t4g.medium the normal choice; the others are used only when it is unavailable.
  #
  # Spot vs On-Demand moved here from the launch template: with a list of sizes AWS reads the market
  # from the group's distribution (a spot block left in the template is refused). var.spot = false
  # (the default since ADR-0028) means 100 percent On-Demand, the escape hatch for when Spot has no
  # capacity anywhere (found live, KAV-32 Lab 28). ASG-managed spot only supports one-time requests.
  mixed_instances_policy {
    instances_distribution {
      on_demand_allocation_strategy            = "prioritized"
      on_demand_base_capacity                  = 0
      on_demand_percentage_above_base_capacity = var.spot ? 0 : 100
      spot_allocation_strategy                 = "capacity-optimized-prioritized"
    }

    launch_template {
      launch_template_specification {
        launch_template_id = aws_launch_template.node.id
        version            = aws_launch_template.node.latest_version
      }

      dynamic "override" {
        for_each = var.instance_types
        content {
          instance_type = override.value
        }
      }
    }
  }

  tag {
    key                 = "Name"
    value               = var.name_prefix
    propagate_at_launch = true
  }

  dynamic "tag" {
    for_each = local.required_tags
    content {
      key                 = tag.key
      value               = tag.value
      propagate_at_launch = true
    }
  }
}
