mock_provider "aws" {
  mock_data "aws_eks_cluster" {
    defaults = {
      endpoint              = "https://example.eks.amazonaws.com"
      certificate_authority = [{ data = "ZmFrZS1jYQ==" }]
      vpc_config            = [{ cluster_security_group_id = "sg-0123456789abcdef0" }]
    }
  }
  mock_resource "aws_cognito_user_pool" {
    defaults = { endpoint = "cognito-idp.ap-northeast-2.amazonaws.com/ap-northeast-2_example" }
  }
}
mock_provider "cloudflare" {}

run "team_auth" {
  command = plan
  assert {
    condition     = aws_cognito_user_pool.headlamp.admin_create_user_config[0].allow_admin_create_user_only
    error_message = "Public self-signup must stay disabled."
  }
  assert {
    condition     = aws_cognito_user_pool_client.headlamp.generate_secret && aws_cognito_user_pool_client.headlamp.allowed_oauth_flows == toset(["code"])
    error_message = "Use a confidential authorization-code client."
  }
  assert {
    condition     = length(aws_eks_identity_provider_config.headlamp) == 2 && alltrue([for c in aws_eks_identity_provider_config.headlamp : c.oidc[0].groups_claim == "cognito:groups" && c.oidc[0].groups_prefix == "headlamp:"])
    error_message = "Both EKS clusters must map the same prefixed team groups."
  }
  assert {
    condition     = length(aws_vpc_security_group_ingress_rule.headlamp_api) == 1 && aws_vpc_security_group_ingress_rule.headlamp_api["charlie-dev-workload"].from_port == 443
    error_message = "Only workload API HTTPS ingress is added."
  }
}
