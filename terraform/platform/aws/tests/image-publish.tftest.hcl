mock_provider "aws" {
  mock_data "aws_caller_identity" { defaults = { account_id = "123456789012" } }
  mock_data "aws_subnets" { defaults = { ids = ["subnet-12345678"] } }
  mock_data "aws_ssm_parameter" { defaults = { insecure_value = "ami-12345678" } }
}
mock_provider "random" {}

run "image_publish_contract" {
  command = plan
  assert {
    condition     = toset(keys(aws_iam_role.image_publish)) == toset(["platform", "frontend"]) && aws_iam_role.image_publish["platform"].name == "hopcharlie-platform-image-publish" && aws_iam_role.image_publish["frontend"].name == "hopcharlie-frontend-image-publish"
    error_message = "There must be exactly one publish role per repository, with the documented names."
  }
  assert {
    condition = alltrue([for key, role in aws_iam_role.image_publish : (
      length(jsondecode(role.assume_role_policy).Statement) == 1 &&
      jsondecode(role.assume_role_policy).Statement[0].Action == "sts:AssumeRoleWithWebIdentity" &&
      jsondecode(role.assume_role_policy).Statement[0].Principal.Federated == "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com" &&
      jsondecode(role.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com" &&
      jsondecode(role.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"] == "repo:2026-Softbank-Hackathon-Charlie@350278309/${key}@${key == "platform" ? "1442049765" : "1441992854"}:ref:refs/heads/main"
    )])
    error_message = "Each role must trust only its own repository's main branch through GitHub OIDC."
  }
  assert {
    condition = alltrue([for key, policy in aws_iam_role_policy.image_publish : (
      length(jsondecode(policy.policy).Statement) == 2 &&
      jsondecode(policy.policy).Statement[0].Resource == "arn:aws:ecr:ap-northeast-2:123456789012:repository/hopcharlie/${key == "platform" ? "platform" : "console"}" &&
      !contains(jsondecode(policy.policy).Statement[0].Action, "ecr:CreateRepository") &&
      !contains(jsondecode(policy.policy).Statement[0].Action, "ecr:DeleteRepository") &&
      jsondecode(policy.policy).Statement[1].Action == "ecr:GetAuthorizationToken" &&
      jsondecode(policy.policy).Statement[1].Resource == "*"
    )])
    error_message = "Each role may push only to its own existing repository."
  }
}
