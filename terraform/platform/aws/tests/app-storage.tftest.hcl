mock_provider "aws" {
  mock_data "aws_caller_identity" { defaults = { account_id = "123456789012" } }
  mock_data "aws_subnets" { defaults = { ids = ["subnet-12345678"] } }
  mock_data "aws_ssm_parameter" { defaults = { insecure_value = "ami-12345678" } }
}
mock_provider "random" {}

run "app_storage_contract" {
  command = plan
  assert {
    condition = (
      aws_iam_policy.app_boundary.name == "hopcharlie-app-boundary" &&
      local.app_boundary_arn == "arn:aws:iam::123456789012:policy/hopcharlie-app-boundary" &&
      length(jsondecode(aws_iam_policy.app_boundary.policy).Statement) == 2 &&
      toset(jsondecode(aws_iam_policy.app_boundary.policy).Statement[0].Action) == toset(["s3:ListBucket", "s3:ListBucketMultipartUploads", "s3:GetBucketLocation"]) &&
      jsondecode(aws_iam_policy.app_boundary.policy).Statement[0].Resource == "arn:aws:s3:::hopcharlie-eks-*" &&
      toset(jsondecode(aws_iam_policy.app_boundary.policy).Statement[1].Action) == toset(["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:GetObjectTagging", "s3:PutObjectTagging", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"]) &&
      jsondecode(aws_iam_policy.app_boundary.policy).Statement[1].Resource == "arn:aws:s3:::hopcharlie-eks-*/*" &&
      alltrue([for st in jsondecode(aws_iam_policy.app_boundary.policy).Statement : st.Condition == { StringEquals = { "s3:ResourceAccount" = "123456789012" } }])
    )
    error_message = "The boundary must allow only object use and bucket listing on this account's hopcharlie-eks-* buckets."
  }
  assert {
    condition = alltrue([for s in jsondecode(aws_iam_policy.app_boundary.policy).Statement : (
      s.Effect == "Allow" && alltrue([for a in s.Action : startswith(a, "s3:") && !strcontains(a, "*")])
    )])
    error_message = "The boundary must list S3 actions only, without wildcards."
  }
  assert {
    condition = (
      length(jsondecode(aws_iam_role_policy.platform_app_storage.policy).Statement) == 11 &&
      jsondecode(aws_iam_role_policy.platform_app_storage.policy).Statement[0].Action == "s3:CreateBucket" &&
      jsondecode(aws_iam_role_policy.platform_app_storage.policy).Statement[0].Resource == "arn:aws:s3:::hopcharlie-eks-*" &&
      jsondecode(aws_iam_role_policy.platform_app_storage.policy).Statement[0].Condition == { StringEquals = { "s3:LocationConstraint" = "ap-northeast-2" } } &&
      jsondecode(aws_iam_role_policy.platform_app_storage.policy).Statement[1].Action == "s3:GetBucketTagging" &&
      jsondecode(aws_iam_role_policy.platform_app_storage.policy).Statement[1].Resource == "arn:aws:s3:::hopcharlie-eks-*" &&
      !can(jsondecode(aws_iam_role_policy.platform_app_storage.policy).Statement[1].Condition) &&
      toset(jsondecode(aws_iam_role_policy.platform_app_storage.policy).Statement[2].Action) == toset(["s3:PutBucketTagging", "s3:PutBucketPublicAccessBlock", "s3:ListBucketVersions", "s3:ListBucketMultipartUploads", "s3:DeleteBucket"]) &&
      jsondecode(aws_iam_role_policy.platform_app_storage.policy).Statement[2].Resource == "arn:aws:s3:::hopcharlie-eks-*" &&
      jsondecode(aws_iam_role_policy.platform_app_storage.policy).Statement[2].Condition == { StringEquals = { "s3:ResourceAccount" = "123456789012" } } &&
      toset(jsondecode(aws_iam_role_policy.platform_app_storage.policy).Statement[3].Action) == toset(["s3:DeleteObject", "s3:DeleteObjectVersion", "s3:AbortMultipartUpload"]) &&
      jsondecode(aws_iam_role_policy.platform_app_storage.policy).Statement[3].Resource == "arn:aws:s3:::hopcharlie-eks-*/*" &&
      jsondecode(aws_iam_role_policy.platform_app_storage.policy).Statement[3].Condition == { StringEquals = { "s3:ResourceAccount" = "123456789012" } }
    )
    error_message = "The platform may create buckets only in its region, read bucket tags, and change, list and empty only this account's hopcharlie-eks-* buckets."
  }
  assert {
    condition = (
      toset(jsondecode(aws_iam_role_policy.platform_app_storage.policy).Statement[7].Action) == toset(["iam:CreateUser", "iam:PutUserPolicy", "iam:DeleteUserPolicy"]) &&
      jsondecode(aws_iam_role_policy.platform_app_storage.policy).Statement[7].Resource == "arn:aws:iam::123456789012:user/hopcharlie-apps/*" &&
      jsondecode(aws_iam_role_policy.platform_app_storage.policy).Statement[7].Condition == { StringEquals = { "iam:PermissionsBoundary" = "arn:aws:iam::123456789012:policy/hopcharlie-app-boundary" } } &&
      toset(jsondecode(aws_iam_role_policy.platform_app_storage.policy).Statement[8].Action) == toset(["iam:TagUser", "iam:GetUser", "iam:DeleteUser", "iam:CreateAccessKey", "iam:DeleteAccessKey", "iam:ListAccessKeys"]) &&
      jsondecode(aws_iam_role_policy.platform_app_storage.policy).Statement[8].Resource == "arn:aws:iam::123456789012:user/hopcharlie-apps/*" &&
      jsondecode(aws_iam_role_policy.platform_app_storage.policy).Statement[9].Action == "iam:GetUser" &&
      jsondecode(aws_iam_role_policy.platform_app_storage.policy).Statement[9].Resource == "arn:aws:iam::123456789012:user/hopcharlie-app-*" &&
      !can(jsondecode(aws_iam_role_policy.platform_app_storage.policy).Statement[9].Condition) &&
      jsondecode(aws_iam_role_policy.platform_app_storage.policy).Statement[10] == { Effect = "Allow", Action = ["s3:GetBucketLocation", "s3:GetReplicationConfiguration"], Resource = "arn:aws:s3:::hopcharlie-eks-*", Condition = { StringEquals = { "s3:ResourceAccount" = "123456789012" } } }
    )
    error_message = "The platform may create users and set their inline policy only under /hopcharlie-apps/ with the boundary, and may only read users by the path-less name pattern. The residency gate may read the location and replication rules of this account's hopcharlie-eks-* buckets."
  }
  assert {
    condition = length(setintersection(
      toset(flatten([for st in jsondecode(aws_iam_role_policy.platform_app_storage.policy).Statement : st.Action])),
      toset(["iam:AttachUserPolicy", "iam:PutUserPermissionsBoundary", "iam:DeleteUserPermissionsBoundary", "iam:UpdateUser", "iam:CreateLoginProfile", "iam:AddUserToGroup", "iam:CreatePolicy", "iam:CreatePolicyVersion", "iam:SetDefaultPolicyVersion", "iam:DeletePolicy", "s3:PutBucketPolicy", "s3:PutBucketAcl", "s3:PutBucketOwnershipControls", "s3:PutObjectAcl"]),
    )) == 0
    error_message = "The platform role must hold no escalation path (managed policies, boundary changes, logins, groups, policy edits) and no bucket policy, ACL or ownership changes."
  }
  assert {
    condition = (
      jsondecode(aws_iam_role_policy.platform_app_storage.policy).Statement[4] == { Effect = "Allow", Action = "s3:ListBucket", Resource = "arn:aws:s3:::hopcharlie-eks-*", Condition = { StringEquals = { "s3:ResourceAccount" = "123456789012" } } } &&
      jsondecode(aws_iam_role_policy.platform_app_storage.policy).Statement[5] == { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject"], Resource = "arn:aws:s3:::hopcharlie-eks-*/*", Condition = { StringEquals = { "s3:ResourceAccount" = "123456789012" } } } &&
      jsondecode(aws_iam_role_policy.platform_app_storage.policy).Statement[6] == { Effect = "Deny", Action = ["s3:GetObject", "s3:PutObject"], Resource = "arn:aws:s3:::hopcharlie-eks-*---pg/*" } &&
      length([for st in jsondecode(aws_iam_role_policy.platform_app_storage.policy).Statement : st if st.Effect == "Allow" && contains(flatten([st.Action]), "s3:GetObject")]) == 1
    )
    error_message = "The platform may list, read and write objects of this account's hopcharlie-eks-* buckets for file moves (ADR 0064), never read or write objects of the ---pg backup buckets, and may delete those objects when it deletes the app."
  }
  assert {
    condition     = random_bytes.app_secrets_key.length == 32 && aws_ssm_parameter.app_secrets_key.name == "/hopcharlie/app-secrets-key" && aws_ssm_parameter.app_secrets_key.type == "SecureString"
    error_message = "The app secrets key must be 32 random bytes in the SecureString /hopcharlie/app-secrets-key."
  }
  assert {
    condition = one([for e in yamldecode(local.host_files["envs.yaml"]).environments : e if e.name == "eks"]).sealedSecretsCert == "/config/sealed-secrets/eks.pem" && one([for e in yamldecode(local.host_files["envs.yaml"]).environments : e if e.name == "eks"]).storage == {
      driver              = "aws"
      bucketPrefix        = "hopcharlie-eks-"
      region              = "ap-northeast-2"
      iamPath             = "/hopcharlie-apps/"
      permissionsBoundary = "arn:aws:iam::123456789012:policy/hopcharlie-app-boundary"
    }
    error_message = "Environment eks must point at its Sealed Secrets certificate and name the bucket prefix, IAM path and boundary that this stack allows."
  }
  assert {
    condition = one([for e in yamldecode(local.host_files["envs.yaml"]).environments : e if e.name == "gke"]).sealedSecretsCert == "/config/sealed-secrets/gke.pem" && one([for e in yamldecode(local.host_files["envs.yaml"]).environments : e if e.name == "gke"]).storage == {
      driver       = "gcs"
      project      = "project-b47cdba3-db5b-42a2-9a6"
      bucketPrefix = "hopcharlie-gke-"
      region       = "asia-northeast3"
    }
    error_message = "Environment gke must use its sealing certificate and the existing Seoul GCS app-storage identity contract."
  }
  assert {
    condition     = aws_ssm_parameter.host_file["envs.yaml"].insecure_value == local.host_files["envs.yaml"] && !strcontains(local.host_files["envs.yaml"], "$${") && !strcontains(local.host_files["envs.yaml"], "\r")
    error_message = "The envs.yaml parameter must be the rendered file, with every placeholder filled and LF line endings."
  }
}
