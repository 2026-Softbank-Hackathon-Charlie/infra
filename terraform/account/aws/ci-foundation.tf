# Loki 저장소만 관리합니다. CI 역할 자체는 관리자 account stack이 소유합니다.
locals { loki_bucket_arn = "arn:aws:s3:::${var.project}-${var.environment}-loki-${var.aws_account_id}-${var.aws_region}" }
resource "aws_iam_role_policy" "foundation" {
  name = "foundation-loki-storage"
  role = aws_iam_role.terraform_apply.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Sid      = "LokiBucketConfiguration", Effect = "Allow",
    Action   = ["s3:ListBucket", "s3:ListBucketVersions", "s3:GetBucket*", "s3:GetAccelerateConfiguration", "s3:GetEncryptionConfiguration", "s3:GetLifecycleConfiguration", "s3:GetReplicationConfiguration", "s3:ListTagsForResource", "s3:CreateBucket", "s3:DeleteBucket", "s3:PutBucketTagging", "s3:TagResource", "s3:UntagResource", "s3:PutEncryptionConfiguration", "s3:PutBucketPublicAccessBlock", "s3:PutBucketPolicy", "s3:DeleteBucketPolicy"],
    Resource = local.loki_bucket_arn
  }] })
}
