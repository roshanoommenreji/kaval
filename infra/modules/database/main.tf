# A database server (ADR-0008). Not a pod in the cluster: its own on-demand t4g.small, its own
# security group, its own EBS data volume. Used by both prod and staging (KAV-57) via a different
# name_prefix/subnet — nothing here is prod-specific except the values infra/envs/prod passes in.
#
# Isolation is by security group, not a private subnet + NAT ($32/mo) — documented here, not
# hidden (ADR-0008). Admin access is SSM Session Manager only; there is no inbound SSH port.

terraform {
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 5.0" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
}

locals {
  roles = ["admin", "gateway", "agent", "executor", "collector"]
}

data "aws_ssm_parameter" "al2023_arm64" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

# Both the instance and the data volume need the AZ; deriving it from the instance itself
# would be a cycle (the volume's AZ needs the instance, the instance's user_data needs the
# volume's ID). Deriving both from the subnet instead breaks that.
data "aws_subnet" "selected" {
  id = var.subnet_id
}

# ─────────────────────────────────────────────────────────────
# Network
# ─────────────────────────────────────────────────────────────

resource "aws_security_group" "database" {
  name        = "${var.name_prefix}-database"
  description = "Inbound 5432 only from the app node security group. No SSH, SSM Session Manager only."
  vpc_id      = var.vpc_id

  ingress {
    description     = "Postgres, from the app node only"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [var.app_node_security_group_id]
  }

  egress {
    description      = "Package installs, image pulls, SSM"
    from_port        = 0
    to_port          = 0
    protocol         = "-1"
    cidr_blocks      = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }

  tags = { Name = "${var.name_prefix}-database" }
}

# ─────────────────────────────────────────────────────────────
# Secrets — one password per Postgres role, generated once, never typed. Read with
# `aws ssm get-parameter --with-decryption`, by Roshan, never by Claude Code (same discipline
# as the Slack tokens, ADR-0026).
# ─────────────────────────────────────────────────────────────

resource "random_password" "db_password" {
  for_each = toset(local.roles)
  length   = 32
  special  = false # goes straight into a shell-invoked docker run / psql connection string
}

resource "aws_ssm_parameter" "db_password" {
  for_each = toset(local.roles)
  name     = "/kaval/${var.name_prefix}/db/${each.key}-password"
  type     = "SecureString"
  value    = random_password.db_password[each.key].result

  tags = { Name = "${var.name_prefix}-db-${each.key}-password" }
}

# ─────────────────────────────────────────────────────────────
# IAM — SSM management, plus read access to exactly this module's own parameters.
# ─────────────────────────────────────────────────────────────

resource "aws_iam_role" "database" {
  name = "${var.name_prefix}-database"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.database.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# Only the admin password is ever read ON the instance (first-boot init). The four
# service-role passwords are read by Roshan, off the instance, to build Kubernetes Secrets.
# The instance role is still scoped to read all five rather than just "admin" — it's the same
# Parameter Store path prefix and splitting it finer buys no real isolation here.
data "aws_iam_policy_document" "read_db_params" {
  statement {
    effect    = "Allow"
    actions   = ["ssm:GetParameter", "ssm:GetParameters"]
    resources = [for p in aws_ssm_parameter.db_password : p.arn]
  }
  statement {
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = ["arn:aws:kms:*:*:alias/aws/ssm"]
  }
}

resource "aws_iam_role_policy" "read_db_params" {
  name   = "read-db-params"
  role   = aws_iam_role.database.id
  policy = data.aws_iam_policy_document.read_db_params.json
}

resource "aws_iam_instance_profile" "database" {
  name = "${var.name_prefix}-database"
  role = aws_iam_role.database.name
}

# ─────────────────────────────────────────────────────────────
# The server
# ─────────────────────────────────────────────────────────────

resource "aws_instance" "database" {
  ami                    = data.aws_ssm_parameter.al2023_arm64.insecure_value
  instance_type          = var.instance_type
  subnet_id              = var.subnet_id
  vpc_security_group_ids = [aws_security_group.database.id]
  iam_instance_profile   = aws_iam_instance_profile.database.name

  associate_public_ip_address          = true                       # outbound only: package installs, SSM, image pulls
  disable_api_termination              = var.termination_protection # accident protection (ADR-0008); true in prod
  instance_initiated_shutdown_behavior = "stop"

  metadata_options {
    http_tokens   = "required" # IMDSv2 only
    http_endpoint = "enabled"
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = var.root_disk_gb
    encrypted   = true
  }

  user_data = base64encode(templatefile("${path.module}/user_data.sh.tftpl", {
    ssh_public_key   = var.ssh_public_key
    db_name          = var.db_name
    admin_param_name = aws_ssm_parameter.db_password["admin"].name
    data_volume_id   = aws_ebs_volume.data.id
  }))

  # A newer AMI or an edited script must not silently replace a running database. Nor must
  # associate_public_ip_address: AWS only reports it back as true while the instance is running
  # (a stopped instance has no public IP), so whenever a plan runs against the normal paused
  # posture it reads back false and -- since this attribute forces replacement -- a plain
  # `terraform apply` while paused would destroy and recreate the real database (found live,
  # 2026-10-06, pre-drill verification before KAV-32's make up/down drill).
  lifecycle {
    ignore_changes = [ami, user_data, associate_public_ip_address]
  }

  tags = {
    Name = "${var.name_prefix}-database"
    Role = "database"
  }
}

# ─────────────────────────────────────────────────────────────
# Data volume — separate from the root disk, so replacing the instance never touches the
# data (ADR-0008). `prevent_destroy` means a `terraform destroy` on this module errors out
# instead of silently deleting it; removing that protection is a deliberate, separate step.
# ─────────────────────────────────────────────────────────────

resource "aws_ebs_volume" "data" {
  availability_zone = data.aws_subnet.selected.availability_zone
  size              = var.data_disk_gb
  type              = "gp3"
  encrypted         = true

  tags = {
    Name = "${var.name_prefix}-database-data"
    Role = "database-data"
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_volume_attachment" "data" {
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.data.id
  instance_id = aws_instance.database.id
}

# ─────────────────────────────────────────────────────────────
# Backups — daily EBS snapshots of the data volume, keep 7 (ADR-0008). This is independent of
# the nightly pg_dump → S3 (scripts/ops/backup.sh): two different recovery mechanisms for two
# different failure modes (whole-volume damage vs. logical/application-level damage).
# ─────────────────────────────────────────────────────────────

resource "aws_iam_role" "dlm" {
  name = "${var.name_prefix}-dlm"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "dlm.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "dlm" {
  role       = aws_iam_role.dlm.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSDataLifecycleManagerServiceRole"
}

resource "aws_dlm_lifecycle_policy" "database" {
  description        = "${var.name_prefix} database data volume daily snapshots keep 7 ADR-0008"
  execution_role_arn = aws_iam_role.dlm.arn
  state              = "ENABLED"

  policy_details {
    resource_types = ["VOLUME"]

    # Name as well as Role: with a staging database in the same account (KAV-57), Role alone
    # made each environment's policy snapshot the other's volume too.
    target_tags = {
      Role = "database-data"
      Name = "${var.name_prefix}-database-data"
    }

    schedule {
      name = "daily"

      create_rule {
        interval      = 24
        interval_unit = "HOURS"
        times         = ["03:00"] # UTC, after the nightly pg_dump (19:30 UTC, a systemd timer on the app node, ADR-0038)
      }

      retain_rule {
        count = 7
      }

      # No tags_to_add: copy_tags already carries Name/Role/Project from the source volume,
      # and a tags_to_add Name here collided with the copied one -- "Duplicate tag key 'Name'
      # specified", which put this policy in ERROR from the day it was created. Found live
      # while building the pre-stop snapshot story, not during planning.
      copy_tags = true
    }
  }

  tags = { Name = "${var.name_prefix}-database-dlm" }
}
