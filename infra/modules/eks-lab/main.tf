# A real EKS cluster for one session (Phase 7, KAV-101, ADR-0067). Ephemeral by design: the control
# plane alone is $0.10 an hour (about $73 a month), so this is created, used and destroyed in a few
# hours, and a timer (infra/modules/lab-reaper) deletes it if a person forgets.
#
# Deliberately plain:
#   - public subnets and public addresses, no NAT gateway (CLAUDE.md); the servers are reached by
#     nobody except through the cluster, and the API only from var.api_allowed_cidrs
#   - one managed node group, On-Demand (a Spot shortage would stall a session that has a timer)
#   - sign-in by EKS access entries, not the old aws-auth ConfigMap
#   - no pod gets AWS rights from the servers' own role: the AWS rights pods need come through IRSA
#     (a role per service account, trusted by the cluster's own identity endpoint)
#
# What is NOT here: Flux, the Kaval chart and the load balancer controller. They are installed on
# top by deploy/gitops/lab-eks (docs/runbooks/lab-eks.md), exactly as staging and production get
# theirs, so the chart that runs here is the chart that runs everywhere.

terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name

  # Written out because the servers an EKS node group launches do not inherit the provider's default
  # tags in every case (the same lesson as infra/modules/node, found live 2026-10-18).
  required_tags = {
    Project   = "kaval"
    Env       = "lab-eks"
    ManagedBy = "terraform"
  }

  # The identity endpoint, without the https:// that IAM's condition keys leave off.
  oidc_host = replace(aws_eks_cluster.this.identity[0].oidc[0].issuer, "https://", "")
}

# ─────────────────────────────────────────────────────────────
# The subnets must say which cluster they belong to and that they face the internet, or the load
# balancer controller cannot choose where to put a load balancer. Done here with tags on the
# subnets the network module made, so that module (which production also uses) does not change.
# ─────────────────────────────────────────────────────────────

resource "aws_ec2_tag" "subnet_cluster" {
  count = length(var.subnet_ids)

  resource_id = var.subnet_ids[count.index]
  key         = "kubernetes.io/cluster/${var.name}"
  value       = "shared"
}

resource "aws_ec2_tag" "subnet_public_elb" {
  count = length(var.subnet_ids)

  resource_id = var.subnet_ids[count.index]
  key         = "kubernetes.io/role/elb"
  value       = "1"
}

# ─────────────────────────────────────────────────────────────
# The control plane
# ─────────────────────────────────────────────────────────────

resource "aws_iam_role" "cluster" {
  name = "${var.name}-cluster"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "eks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "cluster" {
  role       = aws_iam_role.cluster.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}

resource "aws_eks_cluster" "this" {
  name     = var.name
  version  = var.kubernetes_version
  role_arn = aws_iam_role.cluster.arn

  access_config {
    authentication_mode = "API"
    # Whoever runs `terraform apply` can use the cluster afterwards. Nobody else is added.
    bootstrap_cluster_creator_admin_permissions = true
  }

  vpc_config {
    subnet_ids = var.subnet_ids
    # The servers (inside the VPC) talk to the API over the private path; the laptop over the public
    # one, and only from the addresses listed.
    endpoint_private_access = true
    endpoint_public_access  = true
    public_access_cidrs     = var.api_allowed_cidrs
  }

  # Control-plane logs are billed per gigabyte and nobody reads them in a session of a few hours.
  enabled_cluster_log_types = []

  depends_on = [aws_iam_role_policy_attachment.cluster]
}

# ─────────────────────────────────────────────────────────────
# IRSA: the cluster's identity endpoint, trusted by IAM
# ─────────────────────────────────────────────────────────────

resource "aws_iam_openid_connect_provider" "this" {
  url            = aws_eks_cluster.this.identity[0].oidc[0].issuer
  client_id_list = ["sts.amazonaws.com"]
}

# One role per service account. Each trust names the namespace AND the account (`sub`), and the
# audience. Leave `sub` out and any service account in the cluster could use the role.
locals {
  irsa_roles = {
    ebs-csi = {
      namespace       = "kube-system"
      service_account = "ebs-csi-controller-sa"
      managed_policy  = "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
    }
    vpc-cni = {
      namespace       = "kube-system"
      service_account = "aws-node"
      managed_policy  = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
    }
    lbc = {
      namespace       = "kube-system"
      service_account = "aws-load-balancer-controller"
      managed_policy  = null
    }
    executor = {
      namespace       = var.executor_namespace
      service_account = var.executor_service_account
      managed_policy  = null
    }
  }
}

resource "aws_iam_role" "irsa" {
  for_each = local.irsa_roles

  name = "${var.name}-${each.key}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.this.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "${local.oidc_host}:aud" = "sts.amazonaws.com"
          "${local.oidc_host}:sub" = "system:serviceaccount:${each.value.namespace}:${each.value.service_account}"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "irsa_managed" {
  for_each = { for k, v in local.irsa_roles : k => v if v.managed_policy != null }

  role       = aws_iam_role.irsa[each.key].name
  policy_arn = each.value.managed_policy
}

# The load balancer controller's rights: AWS's own published policy for the version pinned in
# deploy/gitops/lab-eks (v3.5.0), unedited, so an upgrade is a re-download and a diff.
resource "aws_iam_role_policy" "lbc" {
  name   = "load-balancer-controller"
  role   = aws_iam_role.irsa["lbc"].id
  policy = file("${path.module}/lbc-iam-policy.json")
}

# The executor's one AWS right in this lab. Today the executor reaches AWS through no API of its own
# (the removal worker is a separate job, ADR-0063), so this proves the mechanism with the smallest
# honest thing: read ONE named parameter. Not a write, not another parameter, not a wildcard.
resource "aws_ssm_parameter" "irsa_proof" {
  name  = "/kaval/${var.name}/irsa-proof"
  type  = "String"
  value = "the executor reached AWS with a short-lived IRSA login and no stored key"
}

resource "aws_iam_role_policy" "executor" {
  name = "read-the-proof-parameter"
  role = aws_iam_role.irsa["executor"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ssm:GetParameter"]
      Resource = [aws_ssm_parameter.irsa_proof.arn]
    }]
  })
}

# ─────────────────────────────────────────────────────────────
# The servers
# ─────────────────────────────────────────────────────────────

resource "aws_iam_role" "node" {
  name = "${var.name}-node"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "node_worker" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"
}

# Pull Kaval's images and nothing else in the registry. EKS servers log in to ECR by themselves, so
# the chart needs no pull secret here (k3s needs one; that is a values difference, not a template one).
resource "aws_iam_role_policy" "node_ecr" {
  name = "pull-kaval-images"
  role = aws_iam_role.node.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"]
        Resource = var.ecr_repository_arns
      },
    ]
  })
}

# ...and AWS's own add-on images (the network plug-in, kube-proxy, DNS, the disk driver): pull only,
# only these repository names, only in AWS's registry account for the region. Found live (Lab 66):
# without this every add-on pod is refused and the servers never become Ready. Kaval's own images
# stay limited to the statement above.
resource "aws_iam_role_policy" "node_ecr_addons" {
  name = "pull-eks-addon-images"
  role = aws_iam_role.node.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"]
        Resource = [
          "arn:aws:ecr:${local.region}:${var.eks_registry_account_id}:repository/eks/*",
          "arn:aws:ecr:${local.region}:${var.eks_registry_account_id}:repository/amazon-k8s-cni*",
          "arn:aws:ecr:${local.region}:${var.eks_registry_account_id}:repository/amazon/*",
        ]
      },
    ]
  })
}

# Session Manager, so a server can be looked at without opening a port (the same way as k3s's nodes).
resource "aws_iam_role_policy_attachment" "node_ssm" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# ─────────────────────────────────────────────────────────────
# Add-ons, in the order the cluster needs them
# ─────────────────────────────────────────────────────────────

# The network and the proxy first: a server cannot become Ready without them.
resource "aws_eks_addon" "vpc_cni" {
  cluster_name             = aws_eks_cluster.this.name
  addon_name               = "vpc-cni"
  service_account_role_arn = aws_iam_role.irsa["vpc-cni"].arn

  # Network policies are enforced by the network add-on itself on EKS (k3s's Flannel ignores them),
  # so this switch is what makes the policies applied in the lab mean anything.
  configuration_values = jsonencode({ enableNetworkPolicy = "true" })

  depends_on = [aws_iam_role_policy_attachment.irsa_managed]
}

resource "aws_eks_addon" "kube_proxy" {
  cluster_name = aws_eks_cluster.this.name
  addon_name   = "kube-proxy"
}

resource "aws_eks_node_group" "this" {
  cluster_name    = aws_eks_cluster.this.name
  node_group_name = "main"
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = var.subnet_ids

  ami_type       = "AL2023_ARM_64_STANDARD"
  instance_types = [var.node_instance_type]
  capacity_type  = "ON_DEMAND"
  disk_size      = var.node_disk_gb

  scaling_config {
    desired_size = var.node_count
    min_size     = 0 # so the timer can scale it to zero
    max_size     = var.node_count
  }

  tags = local.required_tags

  depends_on = [
    aws_iam_role_policy_attachment.node_worker,
    aws_eks_addon.vpc_cni,
    aws_eks_addon.kube_proxy,
  ]
}

# After the servers exist: they run as pods on them.
resource "aws_eks_addon" "coredns" {
  cluster_name = aws_eks_cluster.this.name
  addon_name   = "coredns"
  depends_on   = [aws_eks_node_group.this]
}

# Disks for the in-cluster database (the lab has no separate database server).
resource "aws_eks_addon" "ebs_csi" {
  cluster_name             = aws_eks_cluster.this.name
  addon_name               = "aws-ebs-csi-driver"
  service_account_role_arn = aws_iam_role.irsa["ebs-csi"].arn

  # The disks the driver makes (the lab's database) are made by the cluster, not by Terraform, so
  # they would carry no label at all. This puts the three required labels on each one, so the
  # tag policy holds and the sweep (scripts/ops/lab_sweep.py) can find a leftover by Env.
  configuration_values = jsonencode({
    controller = { extraVolumeTags = local.required_tags }
  })

  depends_on = [aws_eks_node_group.this, aws_iam_role_policy_attachment.irsa_managed]
}
