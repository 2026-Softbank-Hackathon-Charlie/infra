mock_provider "aws" {
  mock_data "aws_ssm_parameter" { defaults = { value = "ami-0123456789abcdef0" } }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/mock-build-role" }
  }
  mock_resource "aws_s3_bucket" {
    defaults = { arn = "arn:aws:s3:::charlie-dev-cnpg-123456789012-ap-northeast-2" }
  }
}

variables {
  aws_account_id = "123456789012"
}

run "cnpg_backup_and_restore_boundaries" {
  command = apply

  assert {
    condition = (
      aws_s3_bucket.cnpg.bucket == "charlie-dev-cnpg-123456789012-ap-northeast-2" &&
      aws_s3_bucket.cnpg.force_destroy == false &&
      aws_s3_bucket_public_access_block.cnpg.block_public_acls &&
      aws_s3_bucket_public_access_block.cnpg.block_public_policy &&
      aws_s3_bucket_public_access_block.cnpg.ignore_public_acls &&
      aws_s3_bucket_public_access_block.cnpg.restrict_public_buckets &&
      one(aws_s3_bucket_server_side_encryption_configuration.cnpg.rule).apply_server_side_encryption_by_default[0].sse_algorithm == "AES256"
    )
    error_message = "CNPG backups require a separate private SSE-S3 bucket without forced deletion."
  }

  assert {
    condition = (
      jsondecode(aws_s3_bucket_policy.cnpg.policy).Statement[0].Effect == "Deny" &&
      jsondecode(aws_s3_bucket_policy.cnpg.policy).Statement[0].Action == "s3:*" &&
      jsondecode(aws_s3_bucket_policy.cnpg.policy).Statement[0].Principal == "*" &&
      jsondecode(aws_s3_bucket_policy.cnpg.policy).Statement[0].Condition.Bool["aws:SecureTransport"] == "false" &&
      toset(jsondecode(aws_s3_bucket_policy.cnpg.policy).Statement[0].Resource) == toset([
        aws_s3_bucket.cnpg.arn, "${aws_s3_bucket.cnpg.arn}/*"
      ])
    )
    error_message = "Both bucket and object operations must reject insecure transport."
  }

  assert {
    condition = alltrue([for purpose, role in aws_iam_role.cnpg :
      role.name == "charlie-dev-cnpg-${purpose}" &&
      jsondecode(role.assume_role_policy).Statement[0].Principal.Service == "pods.eks.amazonaws.com" &&
      toset(jsondecode(role.assume_role_policy).Statement[0].Action) == toset(["sts:AssumeRole", "sts:TagSession"]) &&
      jsonencode(jsondecode(role.assume_role_policy).Statement[0].Condition.StringEquals) == jsonencode({
        "aws:RequestTag/eks-cluster-arn"            = "arn:aws:eks:ap-northeast-2:123456789012:cluster/charlie-dev-workload"
        "aws:RequestTag/eks-cluster-name"           = "charlie-dev-workload"
        "aws:RequestTag/kubernetes-namespace"       = "charlie-db"
        "aws:RequestTag/kubernetes-service-account" = purpose == "source" ? "postgres-poc" : "postgres-poc-restore"
      })
    ])
    error_message = "Only the exact workload cluster/namespace/source or restore service account may assume each runtime role."
  }

  assert {
    condition = alltrue([for policy in aws_iam_role_policy.cnpg :
      length([for statement in jsondecode(policy.policy).Statement : statement if statement.Sid == "BackupBucketMetadata"]) == 1 &&
      alltrue([for statement in jsondecode(policy.policy).Statement :
        statement.Sid != "BackupBucketMetadata" || (
          toset(statement.Action) == toset(["s3:ListBucket", "s3:GetBucketLocation"]) &&
          statement.Resource == aws_s3_bucket.cnpg.arn
        )
      ])
    ])
    error_message = "Barman's HeadBucket metadata permission must stay on the dedicated bucket, not all buckets."
  }

  assert {
    condition = (
      length(jsondecode(aws_iam_role_policy.cnpg["source"].policy).Statement) == 2 &&
      alltrue([for statement in jsondecode(aws_iam_role_policy.cnpg["source"].policy).Statement :
        statement.Sid == "BackupBucketMetadata" || (
          statement.Effect == "Allow" &&
          statement.Resource == "${aws_s3_bucket.cnpg.arn}/source/*" &&
          toset(statement.Action) == toset([
            "s3:GetObject", "s3:PutObject", "s3:DeleteObject",
            "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"
          ])
        )
      ])
    )
    error_message = "The source role must not read/write/delete restore objects or unrelated bucket objects."
  }

  assert {
    condition = (
      length(jsondecode(aws_iam_role_policy.cnpg["restore"].policy).Statement) == 3 &&
      alltrue([for statement in jsondecode(aws_iam_role_policy.cnpg["restore"].policy).Statement :
        statement.Sid == "BackupBucketMetadata" || (
          statement.Sid == "ReadSourceArchive" && statement.Effect == "Allow" &&
          statement.Resource == "${aws_s3_bucket.cnpg.arn}/source/*" &&
          toset(statement.Action) == toset(["s3:GetObject"])
          ) || (
          statement.Sid == "OwnBackupObjects" && statement.Effect == "Allow" &&
          statement.Resource == "${aws_s3_bucket.cnpg.arn}/restore/*" &&
          toset(statement.Action) == toset([
            "s3:GetObject", "s3:PutObject", "s3:DeleteObject",
            "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"
          ])
        )
      ])
    )
    error_message = "Restore may copy/read source archives but must never write/delete them; mutations are confined to restore/."
  }

  assert {
    condition = (
      output.cnpg_bucket_name == aws_s3_bucket.cnpg.bucket &&
      output.cnpg_source_role_arn == aws_iam_role.cnpg["source"].arn &&
      output.cnpg_restore_role_arn == aws_iam_role.cnpg["restore"].arn
    )
    error_message = "Expose only the CNPG bucket and the matching source/restore runtime role contract."
  }
}
