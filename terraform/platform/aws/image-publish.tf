# GitHub Actions roles that publish the platform and console images to ECR
# (ADR 0024). Each role trusts only its own repository's main branch through
# the account's GitHub OIDC provider, and pushes only to its own repository.
# Both workflows open an infra PR that pins the new digest. These roles cannot deploy.
locals {
  github_oidc_provider_arn = "arn:aws:iam::${data.aws_caller_identity.me.account_id}:oidc-provider/token.actions.githubusercontent.com"
  # Immutable subjects: gh api repos/<org>/<repo>/actions/oidc/customization/sub
  # returns use_default, so the subject carries the owner and repository ids.
  image_publishers = {
    platform = {
      subject    = "repo:2026-Softbank-Hackathon-Charlie@350278309/platform@1442049765:ref:refs/heads/main"
      repository = aws_ecr_repository.platform.name
    }
    frontend = {
      subject    = "repo:2026-Softbank-Hackathon-Charlie@350278309/frontend@1441992854:ref:refs/heads/main"
      repository = aws_ecr_repository.console.name
    }
  }
}

resource "aws_iam_role" "image_publish" {
  for_each = local.image_publishers
  name     = "hopcharlie-${each.key}-image-publish"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = local.github_oidc_provider_arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = each.value.subject
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "image_publish" {
  for_each = local.image_publishers
  name     = "push-image"
  role     = aws_iam_role.image_publish[each.key].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage"]
        Resource = "arn:aws:ecr:${var.region}:${data.aws_caller_identity.me.account_id}:repository/${each.value.repository}"
      },
      { Effect = "Allow", Action = "ecr:GetAuthorizationToken", Resource = "*" },
    ]
  })
}

# The platform repository's image scan workflow (platform SEC-04, ADR 0029):
# pulls running images to scan them with Grype. Read only, on every
# repository of the account; it cannot push, delete or deploy.
resource "aws_iam_role" "image_scan" {
  name = "hopcharlie-image-scan"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = local.github_oidc_provider_arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = local.image_publishers.platform.subject
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "image_scan" {
  name = "pull-images"
  role = aws_iam_role.image_scan.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
        Resource = "arn:aws:ecr:${var.region}:${data.aws_caller_identity.me.account_id}:repository/*"
      },
      { Effect = "Allow", Action = "ecr:GetAuthorizationToken", Resource = "*" },
    ]
  })
}
