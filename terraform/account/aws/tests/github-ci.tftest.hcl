mock_provider "aws" {
  mock_resource "aws_iam_openid_connect_provider" {
    defaults = { arn = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com" }
  }
  mock_resource "aws_iam_policy" { defaults = { arn = "arn:aws:iam::123456789012:policy/mock" } }
}
variables {
  aws_account_id             = "123456789012"
  github_oidc_subject_prefix = "repo:example/charlie-infra"
}
run "create_new_provider_and_scope_ci" {
  command = apply
  assert {
    condition     = length(aws_iam_openid_connect_provider.github) == 1 && jsondecode(aws_iam_role.terraform_apply.assume_role_policy).Statement[0].Principal.Federated == aws_iam_openid_connect_provider.github[0].arn
    error_message = "Only new accounts create and trust a new GitHub provider."
  }
  assert {
    condition     = jsondecode(aws_iam_role.terraform_apply.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"] == "repo:example/charlie-infra:ref:refs/heads/main"
    error_message = "Trust only this repository main branch."
  }
  assert {
    condition     = alltrue([for s in jsondecode(aws_iam_role_policy.terraform_apply.policy).Statement : !contains(s.Action, "s3:DeleteBucket") && !contains(s.Action, "s3:DeleteObjectVersion")]) && length(local.deployment_state_keys) == 4 && !contains(local.deployment_state_keys, "account/aws/terraform.tfstate")
    error_message = "CI must not manage account state or delete the state bucket."
  }
  assert {
    condition     = alltrue([for s in jsondecode(aws_iam_role_policy.foundation.policy).Statement : s.Resource == local.loki_bucket_arn && !anytrue([for action in s.Action : startswith(action, "codebuild:") || startswith(action, "iam:")])])
    error_message = "Foundation CI storage policy must be restricted to Loki."
  }
}
run "reuse_existing_provider_without_ownership" {
  command = plan
  variables { github_oidc_provider_arn = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com" }
  assert {
    condition     = length(aws_iam_openid_connect_provider.github) == 0 && jsondecode(aws_iam_role.terraform_apply.assume_role_policy).Statement[0].Principal.Federated == var.github_oidc_provider_arn
    error_message = "Existing provider stays in its original state; only reference the ARN."
  }
}
run "reject_cross_account_provider" {
  command = plan
  variables { github_oidc_provider_arn = "arn:aws:iam::999999999999:oidc-provider/token.actions.githubusercontent.com" }
  expect_failures = [var.github_oidc_provider_arn]
}
