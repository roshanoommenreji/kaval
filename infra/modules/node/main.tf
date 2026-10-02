# The prod node: one spot t4g.medium in an ASG pinned to size 1, running k3s. A launch
# template + ASG rather than a bare instance (unlike infra/modules/devbox) because spot
# capacity can be reclaimed with two minutes' notice — the ASG's job is to replace it
# without anyone paged. See docs/cost/budget-plan.md for the spot math.
#
# What this story does NOT yet give the replacement instance: the Helm releases back.
# Nothing here is GitOps-managed yet (Flux arrives in a follow-on Phase 4 story), so a
# reclamation today means a fresh, empty k3s — `helm upgrade --install` has to be re-run
# by hand. Documented in docs/labs/lab-19-aws-landing.md, not hidden.

terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

data "aws_ssm_parameter" "al2023_arm64" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

resource "aws_security_group" "node" {
  name        = "kaval-prod-node"
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

  tags = { Name = "kaval-prod-node" }
}

resource "aws_launch_template" "node" {
  name_prefix   = "kaval-prod-"
  image_id      = data.aws_ssm_parameter.al2023_arm64.insecure_value
  instance_type = var.instance_type

  iam_instance_profile {
    name = var.instance_profile_name
  }

  vpc_security_group_ids = [aws_security_group.node.id]

  # ASG-managed spot only supports "one-time" requests (confirmed by AWS rejecting
  # "persistent" here at apply time) -- the ASG itself is what re-launches a replacement
  # on interruption, not a persistent spot request underneath it.
  instance_market_options {
    market_type = "spot"
  }

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

  user_data = base64encode(templatefile("${path.module}/user_data.sh.tftpl", {
    ssh_public_key   = var.ssh_public_key
    k3s_version      = var.k3s_version
    k3s_sha256_arm64 = var.k3s_sha256_arm64
  }))

  tag_specifications {
    resource_type = "instance"
    tags          = { Name = "kaval-prod" }
  }

  # A newer AMI or an edited script must not silently replace a running node mid-release.
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_autoscaling_group" "node" {
  name                = "kaval-prod"
  min_size            = 1
  max_size            = 1
  desired_capacity    = 1
  vpc_zone_identifier = [var.subnet_id]
  health_check_type   = "EC2" # no load balancer target to check against

  launch_template {
    id      = aws_launch_template.node.id
    version = aws_launch_template.node.latest_version
  }

  tag {
    key                 = "Name"
    value               = "kaval-prod"
    propagate_at_launch = true
  }

  tag {
    key                 = "Project"
    value               = "kaval"
    propagate_at_launch = true
  }
}
