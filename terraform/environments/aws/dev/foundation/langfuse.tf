variable "enable_langfuse_storage" {
  description = "Keep the deployed dev Langfuse event storage enabled. Disabling requires an approved decommission plan."
  type        = bool
  default     = true
}

locals {
  langfuse_name = "${var.project}-${var.environment}-langfuse"
}

resource "aws_s3_bucket" "langfuse" {
  count         = var.enable_langfuse_storage ? 1 : 0
  bucket        = "${local.langfuse_name}-${var.aws_account_id}-${var.aws_region}"
  force_destroy = false
}

resource "aws_s3_bucket_server_side_encryption_configuration" "langfuse" {
  count  = var.enable_langfuse_storage ? 1 : 0
  bucket = aws_s3_bucket.langfuse[0].id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "langfuse" {
  count                   = var.enable_langfuse_storage ? 1 : 0
  bucket                  = aws_s3_bucket.langfuse[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_policy" "langfuse" {
  count  = var.enable_langfuse_storage ? 1 : 0
  bucket = aws_s3_bucket.langfuse[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport", Effect = "Deny", Principal = "*", Action = "s3:*"
      Resource  = [aws_s3_bucket.langfuse[0].arn, "${aws_s3_bucket.langfuse[0].arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
  depends_on = [aws_s3_bucket_public_access_block.langfuse]
}

resource "aws_iam_role" "langfuse" {
  count = var.enable_langfuse_storage ? 1 : 0
  name  = local.langfuse_name
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow", Action = ["sts:AssumeRole", "sts:TagSession"]
      Principal = { Service = "pods.eks.amazonaws.com" }
      Condition = { StringEquals = {
        "aws:RequestTag/eks-cluster-name"           = var.management_cluster_name
        "aws:RequestTag/kubernetes-namespace"       = "langfuse"
        "aws:RequestTag/kubernetes-service-account" = "langfuse"
      } }
    }]
  })
}

resource "aws_iam_role_policy" "langfuse" {
  count = var.enable_langfuse_storage ? 1 : 0
  name  = "langfuse-events"
  role  = aws_iam_role.langfuse[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "ListEvents", Effect = "Allow", Action = ["s3:ListBucket"]
        Resource  = [aws_s3_bucket.langfuse[0].arn]
        Condition = { StringLike = { "s3:prefix" = ["events/", "events/*"] } }
      },
      {
        Sid      = "ReadWriteEvents", Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject"]
        Resource = ["${aws_s3_bucket.langfuse[0].arn}/events/*"]
      }
    ]
  })
}

output "langfuse_storage" {
  description = "Public Langfuse storage contract; null while disabled. No credentials."
  value = var.enable_langfuse_storage ? {
    bucket   = aws_s3_bucket.langfuse[0].bucket
    role_arn = aws_iam_role.langfuse[0].arn
    prefix   = "events/"
  } : null
}
