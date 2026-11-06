mock_provider "aws" {
  mock_data "aws_caller_identity" { defaults = { account_id = "123456789012" } }
  mock_data "aws_subnets" { defaults = { ids = ["subnet-12345678"] } }
  mock_data "aws_ssm_parameter" { defaults = { insecure_value = "ami-12345678" } }
}
mock_provider "random" {}

run "image_publish_contract" {
  command = plan
  assert {
    condition     = toset(keys(aws_iam_role.image_publish)) == toset(["platform", "frontend", "moimroom"]) && alltrue([for key, role in aws_iam_role.image_publish : role.name == "hopcharlie-${key}-image-publish"])
    error_message = "There must be exactly one publish role per repository, with the documented names."
  }
  assert {
    condition = alltrue([for key, role in aws_iam_role.image_publish : (
      length(jsondecode(role.assume_role_policy).Statement) == 1 &&
      jsondecode(role.assume_role_policy).Statement[0].Action == "sts:AssumeRoleWithWebIdentity" &&
      jsondecode(role.assume_role_policy).Statement[0].Principal.Federated == "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com" &&
      jsondecode(role.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com" &&
      length(keys(jsondecode(role.assume_role_policy).Statement[0].Condition)) == 1 &&
      length(keys(jsondecode(role.assume_role_policy).Statement[0].Condition.StringEquals)) == 2 &&
      jsondecode(role.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"] == "repo:2026-Softbank-Hackathon-Charlie@350278309/${key}@${lookup({ platform = "1442049765", frontend = "1441992854", moimroom = "1442480764" }, key)}:ref:refs/heads/main"
    )])
    error_message = "Each role must trust only its own repository's main branch through GitHub OIDC."
  }
  assert {
    condition = alltrue([for key, policy in aws_iam_role_policy.image_publish : (
      length(jsondecode(policy.policy).Statement) == 2 &&
      jsondecode(policy.policy).Statement[0].Resource == "arn:aws:ecr:ap-northeast-2:123456789012:repository/hopcharlie/${lookup({ platform = "platform", frontend = "console", moimroom = "apps/moimroom" }, key)}" &&
      toset(jsondecode(policy.policy).Statement[0].Action) == toset(["ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage"]) &&
      jsondecode(policy.policy).Statement[1].Action == "ecr:GetAuthorizationToken" &&
      jsondecode(policy.policy).Statement[1].Resource == "*"
    )])
    error_message = "Each role may push only to its own existing repository."
  }
  assert {
    condition     = aws_ecr_repository.moimroom.name == "hopcharlie/apps/moimroom" && aws_ecr_repository.moimroom.image_tag_mutability == "IMMUTABLE" && aws_ecr_repository.moimroom.force_delete == false && one(aws_ecr_repository.moimroom.encryption_configuration).encryption_type == "AES256" && output.moimroom_image_repository_name == "hopcharlie/apps/moimroom"
    error_message = "The moimroom repository must preserve immutable published tags without forced image deletion."
  }
  assert {
    condition = (
      aws_iam_role.image_scan.name == "hopcharlie-image-scan" &&
      length(jsondecode(aws_iam_role.image_scan.assume_role_policy).Statement) == 1 &&
      jsondecode(aws_iam_role.image_scan.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"] == "repo:2026-Softbank-Hackathon-Charlie@350278309/platform@1442049765:ref:refs/heads/main" &&
      jsondecode(aws_iam_role.image_scan.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com" &&
      length(jsondecode(aws_iam_role_policy.image_scan.policy).Statement) == 2 &&
      toset(jsondecode(aws_iam_role_policy.image_scan.policy).Statement[0].Action) == toset(["ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]) &&
      jsondecode(aws_iam_role_policy.image_scan.policy).Statement[0].Resource == "arn:aws:ecr:ap-northeast-2:123456789012:repository/*" &&
      jsondecode(aws_iam_role_policy.image_scan.policy).Statement[1].Action == "ecr:GetAuthorizationToken"
    )
    error_message = "The image scan role must trust only the platform main branch and only pull images."
  }
}
