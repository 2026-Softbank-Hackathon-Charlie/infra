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
# instance. Deleting an app also empties and deletes its backup bucket
# hopcharlie-eks-<app>---pg and its user hopcharlie-app-<app>---pg (platform
# ADR 0025, deletion by platform PR #110 and its ADR 0037). ./tf.sh destroy
# fails on the boundary while a user still has it, and it deletes
# /hopcharlie/app-secrets-key with every stored secret's key.

locals {
  # The bucket prefix and IAM path of environment eks. host/envs.yaml must name
  # the same ones and the boundary below.
  app_bucket_prefix = "hopcharlie-eks-"
  app_iam_path      = "/hopcharlie-apps/"
  app_bucket_arn    = "arn:aws:s3:::${local.app_bucket_prefix}*"
  app_user_arn      = "arn:aws:iam::${data.aws_caller_identity.me.account_id}:user${local.app_iam_path}*"
  app_boundary_name = "hopcharlie-app-boundary"
  app_boundary_arn  = "arn:aws:iam::${data.aws_caller_identity.me.account_id}:policy/${local.app_boundary_name}"
  # Only buckets of this account: a bucket another account named
  # hopcharlie-eks-* is never touched.
  app_own_buckets = { StringEquals = { "s3:ResourceAccount" = data.aws_caller_identity.me.account_id } }
}

# The most an app user can do. The platform's inline policy
# (internal/storage bucketPolicy) narrows it to the instance's own bucket.
# Bucket settings (policy, ACL, public access block, deletion) are left out, so
# even a wrong inline policy cannot open a bucket.
resource "aws_iam_policy" "app_boundary" {
  name        = local.app_boundary_name
  description = "Permissions boundary of the IAM users the HopCharlie platform makes for app buckets"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Action    = ["s3:ListBucket", "s3:ListBucketMultipartUploads", "s3:GetBucketLocation"]
        Resource  = local.app_bucket_arn
        Condition = local.app_own_buckets
      },
      {
        Effect    = "Allow"
        Action    = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:GetObjectTagging", "s3:PutObjectTagging", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"]
        Resource  = "${local.app_bucket_arn}/*"
        Condition = local.app_own_buckets
      },
    ]
  })
}

# Exactly the calls of the platform's aws storage driver (internal/storage/aws.go).
resource "aws_iam_role_policy" "platform_app_storage" {
  name = "app-storage"
  role = aws_iam_role.platform_pod.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # Create a bucket in this region. No ACL or object ownership: new
      # buckets keep ACLs off.
      {
        Effect    = "Allow"
        Action    = "s3:CreateBucket"
        Resource  = local.app_bucket_arn
        Condition = { StringEquals = { "s3:LocationConstraint" = var.region } }
      },
      # Read the app tag. Also how the driver finds a missing bucket: without
      # a bucket there is no s3:ResourceAccount, so a condition would turn
      # NoSuchBucket into AccessDenied. Read only.
      {
        Effect   = "Allow"
        Action   = "s3:GetBucketTagging"
        Resource = local.app_bucket_arn
      },
      # Tag, close to the public, list what is left and delete the instances' buckets.
      {
        Effect    = "Allow"
        Action    = ["s3:PutBucketTagging", "s3:PutBucketPublicAccessBlock", "s3:ListBucketVersions", "s3:ListBucketMultipartUploads", "s3:DeleteBucket"]
        Resource  = local.app_bucket_arn
        Condition = local.app_own_buckets
      },
      # Empty a bucket before deleting it: every version and delete marker
      # (DeleteObjects with VersionId) and unfinished multipart uploads.
      {
        Effect    = "Allow"
        Action    = ["s3:DeleteObject", "s3:DeleteObjectVersion", "s3:AbortMultipartUpload"]
        Resource  = "${local.app_bucket_arn}/*"
        Condition = local.app_own_buckets
      },
      # Move registered apps' files with rclone (remote eks:, platform
      # PLATFORM_APP_FILES, ADR 0064): list, read and write objects. With the
      # DeleteObject above, rclone sync can also remove extra objects.
      {
        Effect    = "Allow"
        Action    = "s3:ListBucket"
        Resource  = local.app_bucket_arn
        Condition = local.app_own_buckets
      },
      {
        Effect    = "Allow"
        Action    = ["s3:GetObject", "s3:PutObject"]
        Resource  = "${local.app_bucket_arn}/*"
        Condition = local.app_own_buckets
      },
      # Backup buckets hopcharlie-eks-<app>---pg (platform ADR 0025) share the
      # prefix; the platform never reads or writes their objects. It may delete
      # them when it deletes the app (platform PR #110).
      {
        Effect   = "Deny"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = "${local.app_bucket_arn}---pg/*"
      },
      # Create a user, and change its inline policy, only with the boundary.
      # Nobody else creates users under this path (ADR 0035).
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
      # Read a user by name only. IAM cannot know the path of a user that does
      # not exist, so GetUser on a missing one is checked against the path-less
      # ARN; without this it is AccessDenied, not NoSuchEntity, and a retried
      # Delete never finishes. Read only.
      {
        Effect   = "Allow"
        Action   = "iam:GetUser"
        Resource = "arn:aws:iam::${data.aws_caller_identity.me.account_id}:user/hopcharlie-app-*"
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
