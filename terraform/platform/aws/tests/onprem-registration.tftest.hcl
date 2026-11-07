mock_provider "aws" {
  mock_data "aws_caller_identity" { defaults = { account_id = "123456789012" } }
  mock_data "aws_subnets" { defaults = { ids = ["subnet-12345678"] } }
  mock_data "aws_ssm_parameter" { defaults = { insecure_value = "ami-12345678" } }
}
mock_provider "aws" { alias = "second" }
mock_provider "random" {}

run "registration_disabled" {
  command = plan
  assert {
    condition     = length(aws_iam_role.platform_api) == 0 && length(aws_eks_pod_identity_association.platform_api) == 0 && length(aws_iam_role_policy.platform_legacy_deny_registration_oauth) == 0
    error_message = "Default-off must not replace the existing SA identity or change its policy."
  }
}

run "registration_api_identity" {
  command = plan
  variables { onprem_registration_enabled = true }
  assert {
    condition     = aws_eks_pod_identity_association.platform_api[0].service_account == "hopcharlie-platform-api" && aws_eks_pod_identity_association.platform_api[0].namespace == "charlie-platform" && aws_eks_pod_identity_association.platform.service_account == "hopcharlie-platform"
    error_message = "Only the API switches identity; the legacy association is preserved."
  }
  assert {
    condition     = jsondecode(aws_iam_role_policy.platform_legacy_deny_registration_oauth[0].policy).Statement[0].Effect == "Deny" && jsondecode(aws_iam_role_policy.platform_legacy_deny_registration_oauth[0].policy).Statement[0].Action == "ssm:GetParameter*" && endswith(jsondecode(aws_iam_role_policy.platform_legacy_deny_registration_oauth[0].policy).Statement[0].Resource, "/onprem-registration-oauth")
    error_message = "Legacy identity cannot retrieve the dedicated OAuth credential through any GetParameter action."
  }
  assert {
    condition     = jsondecode(aws_iam_role_policy.platform_legacy_deny_registration_oauth[0].policy).Statement[1].Action == "ssm:GetParametersByPath" && length(jsondecode(aws_iam_role_policy.platform_legacy_deny_registration_oauth[0].policy).Statement[1].NotResource) == 4 && alltrue([for arn in jsondecode(aws_iam_role_policy.platform_legacy_deny_registration_oauth[0].policy).Statement[1].NotResource : can(regex("/(kube|sealed-secrets)(/\\*)?$", arn))])
    error_message = "Ancestor recursive queries stay denied; only kube and sealed-secrets enumeration remain exempt."
  }
  assert {
    condition     = length(aws_iam_role_policy.platform_api_existing) == 4 && aws_iam_role_policy.platform_api_existing["storage"].policy == aws_iam_role_policy.platform_app_storage.policy && aws_iam_role_policy.platform_api_existing["build"].policy == aws_iam_role_policy.platform_app_build.policy
    error_message = "API must retain existing storage/build/security/settings behavior with copied policies."
  }
}
