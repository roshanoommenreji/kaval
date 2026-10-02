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

# Untagged images (left behind when a tag is reused in a *different* repo push, or by a
# failed push) age out. Tagged sha-* images are never touched by this policy — they are
# the record of every artifact that was ever built, which is the point of immutable tags.
resource "aws_ecr_lifecycle_policy" "this" {
  for_each   = aws_ecr_repository.this
  repository = each.value.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "expire untagged images"
      selection = {
        tagStatus   = "untagged"
        countType   = "sinceImagePushed"
        countUnit   = "days"
        countNumber = var.untagged_expire_days
      }
      action = { type = "expire" }
    }]
  })
}
