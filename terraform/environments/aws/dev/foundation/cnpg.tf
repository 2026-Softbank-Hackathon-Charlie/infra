# CNPG PoC backups are separate from state, Loki and the management platform DB.
# Barman owns retention. Do not expire WAL independently with an S3 lifecycle.
locals {
  cnpg_name = "${var.project}-${var.environment}-cnpg"
  cnpg_service_accounts = {
    source  = "postgres-poc"
    restore = "postgres-poc-restore"
  }
  cnpg_object_actions = [
    "s3:GetObject", "s3:PutObject", "s3:DeleteObject",
    "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts",
  ]
}

resource "aws_s3_bucket" "cnpg" {
  bucket        = "${local.cnpg_name}-${var.aws_account_id}-${var.aws_region}"
  force_destroy = false
  tags          = { Component = "database" }

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "cnpg" {
  bucket = aws_s3_bucket.cnpg.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "cnpg" {
  bucket = aws_s3_bucket.cnpg.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_policy" "cnpg" {
  bucket = aws_s3_bucket.cnpg.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.cnpg.arn, "${aws_s3_bucket.cnpg.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })

  depends_on = [aws_s3_bucket_public_access_block.cnpg]
}

resource "aws_iam_role" "cnpg" {
  for_each    = local.cnpg_service_accounts
  name        = "${local.cnpg_name}-${each.key}"
  description = "CNPG PoC ${each.key}: dedicated workload service account and backup prefix"
  tags        = { Component = "database" }

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["sts:AssumeRole", "sts:TagSession"]
      Principal = {
        Service = "pods.eks.amazonaws.com"
      }
      Condition = { StringEquals = {
        "aws:RequestTag/eks-cluster-arn"            = "arn:aws:eks:${var.aws_region}:${var.aws_account_id}:cluster/${var.workload_cluster_name}"
        "aws:RequestTag/eks-cluster-name"           = var.workload_cluster_name
        "aws:RequestTag/kubernetes-namespace"       = "charlie-db"
        "aws:RequestTag/kubernetes-service-account" = each.value
      } }
    }]
  })
}

resource "aws_iam_role_policy" "cnpg" {
  for_each = local.cnpg_service_accounts
  name     = "cnpg-backup-storage"
  role     = aws_iam_role.cnpg[each.key].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      # HeadBucket uses ListBucket without a prefix. A prefix condition here
      # breaks Barman's bucket-existence check. Both roles can see key metadata
      # in this dedicated bucket; object contents and mutations stay isolated.
      {
        Sid      = "BackupBucketMetadata"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = aws_s3_bucket.cnpg.arn
      },
      {
        Sid      = "OwnBackupObjects"
        Effect   = "Allow"
        Action   = local.cnpg_object_actions
        Resource = "${aws_s3_bucket.cnpg.arn}/${each.key == "source" ? "source" : "restore"}/*"
      },
      ], each.key == "restore" ? [{
        Sid      = "ReadSourceArchive"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${aws_s3_bucket.cnpg.arn}/source/*"
    }] : [])
  })
}
