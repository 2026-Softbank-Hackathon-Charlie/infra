# Buckets and keys the platform makes for every app instance with an s3
# resource (platform ADR 0016), and the key it encrypts app secrets with
# (platform ADR 0019). Infra ADR 0035 records the scope.
#
# The platform pod role (eks.tf) creates the bucket hopcharlie-eks-<instance>
# and the IAM user hopcharlie-app-<instance> under /hopcharlie-apps/ with an
# inline policy and an access key for that bucket. It can create a user only
# with the boundary below, so whatever inline policy it writes, the user can
# do no more than use the objects of hopcharlie-eks-* buckets. It cannot attach
# managed policies, change or remove a boundary, or touch any other user.
#
# Cost: none for IAM and the parameter; each bucket is billed for what it stores.
# Cleanup: the platform deletes an instance's key, user and bucket with the
# instance. ./tf.sh destroy fails on the boundary while a user still has it.

locals {
  app_bucket_arn    = "arn:aws:s3:::hopcharlie-eks-*" # bucketPrefix of environment eks (host/envs.yaml)
  app_user_arn      = "arn:aws:iam::${data.aws_caller_identity.me.account_id}:user/hopcharlie-apps/*"
  app_boundary_name = "hopcharlie-app-boundary"
  app_boundary_arn  = "arn:aws:iam::${data.aws_caller_identity.me.account_id}:policy/${local.app_boundary_name}"
}

# The most an app user can do. The platform's inline policy narrows it to the
# instance's own bucket. Bucket settings (policy, ACL, public access block,
# deletion) are left out, so even a wrong inline policy cannot open a bucket.
resource "aws_iam_policy" "app_boundary" {
  name        = local.app_boundary_name
  description = "Permissions boundary of the IAM users the HopCharlie platform makes for app buckets"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:ListBucketMultipartUploads", "s3:GetBucketLocation"]
        Resource = local.app_bucket_arn
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:GetObjectTagging", "s3:PutObjectTagging", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"]
        Resource = "${local.app_bucket_arn}/*"
      },
    ]
  })
}

resource "aws_iam_role_policy" "platform_app_storage" {
  name = "app-storage"
  role = aws_iam_role.platform_pod.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # Create, tag, close to the public and delete the instances' buckets.
      {
        Effect   = "Allow"
        Action   = ["s3:CreateBucket", "s3:PutBucketTagging", "s3:PutBucketPublicAccessBlock", "s3:GetBucketLocation", "s3:ListBucket", "s3:DeleteBucket"]
        Resource = local.app_bucket_arn
      },
      # Empty a bucket before deleting it.
      {
        Effect   = "Allow"
        Action   = "s3:DeleteObject"
        Resource = "${local.app_bucket_arn}/*"
      },
      # Create a user, and change its inline policy, only with the boundary.
      {
        Effect    = "Allow"
        Action    = ["iam:CreateUser", "iam:PutUserPolicy", "iam:DeleteUserPolicy"]
        Resource  = local.app_user_arn
        Condition = { StringEquals = { "iam:PermissionsBoundary" = local.app_boundary_arn } }
      },
      # Tag (also needed to create with tags), read and delete the users and their keys.
      {
        Effect   = "Allow"
        Action   = ["iam:TagUser", "iam:GetUser", "iam:DeleteUser", "iam:CreateAccessKey", "iam:DeleteAccessKey", "iam:ListAccessKeys"]
        Resource = local.app_user_arn
      },
    ]
  })
}

# The key the platform API encrypts app secrets with: 32 random bytes,
# base64. The API's init container puts it in PLATFORM_SECRETS_KEY. It is
# only here and in the state; replacing it makes every stored secret
# unreadable, so stop if a plan replaces random_bytes.app_secrets_key.
resource "random_bytes" "app_secrets_key" {
  length = 32
}

resource "aws_ssm_parameter" "app_secrets_key" {
  name  = "${local.prefix}/app-secrets-key"
  type  = "SecureString"
  value = random_bytes.app_secrets_key.base64
}
