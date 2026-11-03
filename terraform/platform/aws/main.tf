# AWS resources of the HopCharlie platform that runs on the management EKS
# cluster (ADR 0016): the image registries, the generated secrets and the
# settings the platform pods read from SSM. The EC2 management host that ran
# the platform with Docker Compose was retired on 2026-11-03 (ADR 0026).

data "aws_caller_identity" "me" {}

locals {
  name   = "hopcharlie"
  prefix = "/hopcharlie"
}

# --- image registry ---

resource "aws_ecr_repository" "platform" {
  name                 = "${local.name}/platform"
  image_tag_mutability = "MUTABLE"
  force_delete         = true # destroy must not stop at leftover images

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "platform" {
  repository = aws_ecr_repository.platform.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "keep the last 10 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 10 }
      action       = { type = "expire" }
    }]
  })
}

resource "aws_ecr_repository" "console" {
  name                 = "${local.name}/console"
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "console" {
  repository = aws_ecr_repository.console.name
  policy     = aws_ecr_lifecycle_policy.platform.policy
}

resource "aws_ecr_repository" "moimroom" {
  name                 = "${local.name}/apps/moimroom"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false

  encryption_configuration {
    encryption_type = "AES256"
  }
}

# --- secrets: generated here, stored as SSM SecureString, read by the platform pods ---

resource "random_password" "db" {
  length  = 32
  special = false
}

resource "random_password" "gateway_admin" {
  length  = 40
  special = false
}

resource "random_password" "operator" {
  for_each = toset(var.operators)
  length   = 40
  special  = false
}

resource "random_password" "planner" {
  length  = 40
  special = false
}

resource "random_password" "viewer" {
  length  = 40
  special = false
}

locals {
  platform_tokens = join(",", concat(
    [for n in var.operators : "${n}:operator:${random_password.operator[n].result}"],
    ["planner:agent:${random_password.planner.result}", "screen:viewer:${random_password.viewer.result}"],
  ))
}

resource "aws_ssm_parameter" "db_password" {
  name  = "${local.prefix}/db-password"
  type  = "SecureString"
  value = random_password.db.result
}

resource "aws_ssm_parameter" "gateway_token" {
  name  = "${local.prefix}/gateway-admin-token"
  type  = "SecureString"
  value = random_password.gateway_admin.result
}

resource "aws_ssm_parameter" "platform_tokens" {
  name  = "${local.prefix}/platform-tokens"
  type  = "SecureString"
  value = local.platform_tokens
}

# --- settings files the platform API pod reads (fetch-settings.sh in the
# infra chart): environments and the apps a migration may move. The names keep
# the host/ prefix of the retired EC2 host so existing readers still work. ---

locals {
  # Line endings are normalized so a Windows checkout (CRLF) and a Linux one
  # produce the same parameters, and plan shows no change between them.
  host_files = {
    "envs.yaml" = replace(file("${path.module}/host/envs.yaml"), "\r\n", "\n")
    "apps.yaml" = replace(file("${path.module}/host/apps.yaml"), "\r\n", "\n")
  }
}

resource "aws_ssm_parameter" "host_file" {
  for_each       = local.host_files
  name           = "${local.prefix}/host/${each.key}"
  type           = "String"
  tier           = "Intelligent-Tiering" # unchanged from the host era, so these parameters are not rewritten
  insecure_value = each.value
}

# Team-managed secrets: Terraform only creates them. Set the real values with
# aws ssm put-parameter --overwrite (deploy/aws/README.md); apply never resets them.
resource "aws_ssm_parameter" "team_secret" {
  for_each = {
    "db-passwords" = "# Database passwords plans reference, one KEY=value per line:\n# SRC_PGPASSWORD=...\n# GKE_MOIMROOM_PGPASSWORD=...\n"
    "rclone-env"   = "# rclone remotes, one KEY=value per line:\n# RCLONE_CONFIG_GCS_TYPE=google cloud storage\n"
  }
  name  = "${local.prefix}/${each.key}"
  type  = "SecureString"
  tier  = "Intelligent-Tiering"
  value = each.value

  lifecycle {
    ignore_changes = [value]
  }
}

# Policies of the platform pod role (eks.tf).
locals {
  # Exactly the read calls the AWS collector of GET /v1/security makes
  # (internal/security/aws.go), and nothing else: no write action, and none of
  # the other reads the managed SecurityAudit policy would add. These actions
  # take no resource-level permissions, so the resource is "*".
  # s3control GetPublicAccessBlock is the IAM action s3:GetAccountPublicAccessBlock.
  security_read_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "iam:GetAccountSummary",
        "iam:ListUsers",
        "iam:GetLoginProfile",
        "iam:ListMFADevices",
        "iam:ListAccessKeys",
        "ec2:DescribeSecurityGroups",
        "ec2:GetEbsEncryptionByDefault",
        "cloudtrail:DescribeTrails",
        "cloudtrail:GetTrailStatus",
        "guardduty:ListDetectors",
        "guardduty:GetDetector",
        "guardduty:ListFindings",
        "guardduty:GetFindings",
        "securityhub:DescribeHub",
        "securityhub:GetFindings",
        "s3:GetAccountPublicAccessBlock",
        "sts:GetCallerIdentity",
      ]
      Resource = "*"
    }]
  })

  own_parameters_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
        Resource = "arn:aws:ssm:${var.region}:${data.aws_caller_identity.me.account_id}:parameter${local.prefix}/*"
      },
      {
        Effect    = "Allow"
        Action    = "kms:Decrypt"
        Resource  = "*"
        Condition = { StringEquals = { "kms:ViaService" = "ssm.${var.region}.amazonaws.com" } }
      },
    ]
  })
}
