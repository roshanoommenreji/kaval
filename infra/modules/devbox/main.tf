# A development server: the Phase 1–3 stack runs here instead of on the laptop (ADR-0007).
#
# - Same size as the future prod node (t4g.medium, Graviton, 4 GB), so memory and speed
#   measurements are the real ones.
# - No inbound ports. It is reached only through SSM Session Manager, which the instance
#   dials out to; SSH is tunnelled through that connection.
# - Stops itself after `idle_minutes` with no session, so it only bills while used.
# - Lives in the default VPC. The proper network module arrives in Phase 4.

terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
  filter {
    name   = "availability-zone"
    values = [var.availability_zone]
  }
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

data "aws_ssm_parameter" "al2023_arm64" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

resource "aws_security_group" "devbox" {
  name        = "kaval-devbox"
  description = "No inbound rules. Access is through SSM Session Manager only."
  vpc_id      = data.aws_vpc.default.id

  egress {
    description      = "Package installs, image pulls, model downloads, SSM"
    from_port        = 0
    to_port          = 0
    protocol         = "-1"
    cidr_blocks      = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }
}

resource "aws_iam_role" "devbox" {
  name = "kaval-devbox"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# The only permission the instance holds: registering with Session Manager.
resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.devbox.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "devbox" {
  name = "kaval-devbox"
  role = aws_iam_role.devbox.name
}

resource "aws_instance" "devbox" {
  ami                    = data.aws_ssm_parameter.al2023_arm64.insecure_value
  instance_type          = var.instance_type
  subnet_id              = data.aws_subnets.default.ids[0]
  vpc_security_group_ids = [aws_security_group.devbox.id]
  iam_instance_profile   = aws_iam_instance_profile.devbox.name

  associate_public_ip_address          = true   # outbound internet without a $32/mo NAT Gateway
  instance_initiated_shutdown_behavior = "stop" # the idle timer's `shutdown` stops, never terminates

  metadata_options {
    http_tokens   = "required" # IMDSv2 only
    http_endpoint = "enabled"
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = var.disk_gb
    encrypted   = true
  }

  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    ssh_public_key = var.ssh_public_key
    idle_minutes   = var.idle_minutes
  })

  # A newer Amazon Linux image must not silently rebuild the box and wipe its disk.
  lifecycle {
    ignore_changes = [ami, user_data]
  }

  tags = {
    Name = "kaval-devbox"
  }
}
