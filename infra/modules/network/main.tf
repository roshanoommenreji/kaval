# Prod's own VPC. The dev server deliberately lives in the account's default VPC
# (infra/modules/devbox) since it's disposable; prod gets a real one.
#
# One public subnet per AZ (KAV-51), no NAT Gateway ($32/mo — see docs/cost/budget-plan.md).
# The node gets a public IP for outbound internet (image pulls, package installs) and is
# reached only through SSM Session Manager; nothing needs a private subnet to reach it.
# Three subnets, not one, because the ASG only ever runs a single instance in whichever
# one AZ has spot capacity at launch time — see variables.tf's availability_zones comment.

terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "kaval-prod" }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "kaval-prod" }
}

resource "aws_subnet" "public" {
  for_each = { for idx, az in var.availability_zones : az => var.public_subnet_cidrs[idx] }

  vpc_id                  = aws_vpc.this.id
  cidr_block              = each.value
  availability_zone       = each.key
  map_public_ip_on_launch = true

  tags = { Name = "kaval-prod-public-${each.key}" }
}

# One route table, shared by every AZ's subnet — routing to the internet gateway doesn't
# vary by AZ.
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }

  tags = { Name = "kaval-prod-public" }
}

resource "aws_route_table_association" "public" {
  for_each = aws_subnet.public

  subnet_id      = each.value.id
  route_table_id = aws_route_table.public.id
}
