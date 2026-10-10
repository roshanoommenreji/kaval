# One repo per service, immutable tags. "Build once, promote the artifact" (ADR-0004) only
# holds if a tag can never be silently overwritten — immutability makes that a guarantee
# ECR enforces, not a convention CI has to uphold on its own.

terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

resource "aws_ecr_repository" "this" {
  for_each = toset(var.repository_names)

  name                 = "kaval/${each.value}"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = { Name = "kaval-${each.value}" }
}

# Two rules (KAV-68, ADR-0036):
#   1. Untagged images (left behind by a failed push) age out after a week.
#   2. Only the newest var.keep_tagged_images tagged sha-* images are kept per repository.
#      ECR cannot be told "except what prod runs", so the number has to be large enough that
#      a parked prod's pin stays inside it; `make up` checks that before it starts anything.
resource "aws_ecr_lifecycle_policy" "this" {
  for_each   = aws_ecr_repository.this
  repository = each.value.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "expire untagged images"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = var.untagged_expire_days
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "keep the newest ${var.keep_tagged_images} tagged images"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["sha-"]
          countType     = "imageCountMoreThan"
          countNumber   = var.keep_tagged_images
        }
        action = { type = "expire" }
      },
    ]
  })
}
