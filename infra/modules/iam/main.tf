# The node's own operational role — SSM management and ECR pulls. This is deliberately
# NOT the agent/executor read-only/scoped-write split described in CLAUDE.md constraint #3:
# that split already exists and is enforced today, at the Kubernetes RBAC layer (ADR-0021,
# KAV-47) — ServiceAccounts and Roles scoped per-pod inside the cluster. A per-service AWS
# IAM identity for workloads (IRSA or equivalent) only becomes meaningful once the executor
# calls AWS write APIs directly, which is Phase 6's FinOps work (the "aws-apis" node in
# architecture.toml, phase 6, IRSA on EKS in Phase 7). Until then there is exactly one AWS
# identity in play: the instance itself, and it needs only two things — to be managed via
# SSM, and to pull the images CI pushed.

terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

resource "aws_iam_role" "node" {
  name = "kaval-prod-node"
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
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "ecr_pull" {
  name = "ecr-pull"
  role = aws_iam_role.node.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # GetAuthorizationToken has no resource-level permissions in IAM — AWS requires "*".
        Sid      = "EcrAuth"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Sid    = "EcrPull"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
        ]
        Resource = var.ecr_repository_arns
      }
    ]
  })
}

resource "aws_iam_role_policy" "backup_bucket" {
  name = "backup-bucket"
  role = aws_iam_role.node.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "BackupBucketObjects"
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"]
        Resource = "${var.backup_bucket_arn}/*"
      },
      {
        Sid      = "BackupBucketList"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = var.backup_bucket_arn
      }
    ]
  })
}

resource "aws_iam_role_policy" "read_app_secrets" {
  name = "read-app-secrets"
  role = aws_iam_role.node.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadAppSecretParams"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter", "ssm:GetParameters"]
        Resource = var.secret_parameter_arns
      },
      {
        Sid      = "DecryptAppSecretParams"
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = "arn:aws:kms:*:*:alias/aws/ssm"
      }
    ]
  })
}

resource "aws_iam_instance_profile" "node" {
  name = "kaval-prod-node"
  role = aws_iam_role.node.name
}
