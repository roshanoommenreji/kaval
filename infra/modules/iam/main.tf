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
  name = "${var.name_prefix}-node"
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
      },
      {
        # The bootstrap script looks the database server up by its Role=database tag
        # (KAV-56) rather than a hardcoded IP. DescribeInstances has no resource-level
        # permissions in IAM -- AWS requires "*", same limitation as EcrAuth above. Found
        # live: the first real node replacement after this script shipped failed here
        # with UnauthorizedOperation, aborting the whole bootstrap before it reached
        # Flux's own source.yaml/helmrelease.yaml/sync.yaml apply.
        Sid      = "DescribeDatabaseInstance"
        Effect   = "Allow"
        Action   = ["ec2:DescribeInstances"]
        Resource = "*"
      }
    ]
  })
}

# The node's own "still alive" datapoint (KAV-83, ADR-0053): one namespace, one verb. PutMetricData
# has no resource-level permission, so the only fence is the namespace condition; it cannot read
# metrics, change an alarm or write to any other namespace.
resource "aws_iam_role_policy" "heartbeat" {
  name = "still-alive-heartbeat"
  role = aws_iam_role.node.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "PutOnlyTheStillAliveMetric"
        Effect   = "Allow"
        Action   = ["cloudwatch:PutMetricData"]
        Resource = "*"
        Condition = {
          StringEquals = { "cloudwatch:namespace" = "Kaval/Heartbeat" }
        }
      }
    ]
  })
}

# The server's timer carries the executor's removal order out of the cluster (KAV-95, ADR-0063): it
# may write exactly the orders parameter and nothing else. The worker, not the server, deletes things.
resource "aws_iam_role_policy" "write_removal_orders" {
  count = length(var.removal_orders_parameter_arns) > 0 ? 1 : 0
  name  = "write-removal-orders"
  role  = aws_iam_role.node.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "WriteOnlyTheRemovalOrders"
        Effect   = "Allow"
        Action   = ["ssm:PutParameter"]
        Resource = var.removal_orders_parameter_arns
      }
    ]
  })
}

resource "aws_iam_instance_profile" "node" {
  name = "${var.name_prefix}-node"
  role = aws_iam_role.node.name
}
