mock_provider "aws" {
  mock_resource "aws_iam_role" { defaults = { arn = "arn:aws:iam::123456789012:role/mock-role" } }
  mock_resource "aws_iam_policy" { defaults = { arn = "arn:aws:iam::123456789012:policy/mock-policy" } }
  mock_resource "aws_iam_openid_connect_provider" { defaults = { arn = "arn:aws:iam::123456789012:oidc-provider/oidc.eks.ap-northeast-2.amazonaws.com/id/MOCK" } }
  mock_resource "aws_launch_template" { defaults = { latest_version = 1, id = "lt-0123456789abcdef0" } }
  mock_resource "aws_eks_cluster" { defaults = {
    arn                   = "arn:aws:eks:ap-northeast-2:123456789012:cluster/mock"
    endpoint              = "https://example.eks.amazonaws.com"
    certificate_authority = [{ data = "Y2VydA==" }]
    identity              = [{ oidc = [{ issuer = "https://oidc.eks.ap-northeast-2.amazonaws.com/id/MOCK" }] }]
  } }
}
override_data {
  target = data.terraform_remote_state.foundation
  values = { outputs = {
    vpc_id                                  = "vpc-0123456789abcdef0"
    management_cluster_name                 = "charlie-dev-management"
    workload_cluster_name                   = "charlie-dev-workload"
    management_subnet_ids_by_az             = { ap-northeast-2a = "subnet-0123456789abcdef0", ap-northeast-2c = "subnet-0123456789abcdef1" }
    workload_subnet_ids_by_az               = { ap-northeast-2a = "subnet-0123456789abcdef2", ap-northeast-2c = "subnet-0123456789abcdef3" }
    management_api_source_security_group_id = "sg-0123456789abcdef1"
    workload_api_target_security_group_id   = "sg-0123456789abcdef2"
    ssm_bridge_instance_id                  = "i-0123456789abcdef0"
    ssm_bridge_security_group_id            = "sg-0123456789abcdef3"
    argocd_management_role_arn              = "arn:aws:iam::123456789012:role/argocd"
    argocd_deploy_role_arns                 = { management = "arn:aws:iam::123456789012:role/argocd-management-deploy", workload = "arn:aws:iam::123456789012:role/argocd-workload-deploy" }
    loki_role_arn                           = "arn:aws:iam::123456789012:role/loki"
    langfuse_storage                        = { role_arn = "arn:aws:iam::123456789012:role/langfuse", bucket = "langfuse-test", prefix = "events/" }
  } }
}
variables {
  aws_account_id                     = "123456789012"
  operator_principal_arn             = "arn:aws:iam::123456789012:role/operator"
  additional_operator_principal_arns = ["arn:aws:iam::123456789012:user/second-operator"]
}
run "langfuse_disabled" {
  command = plan
  variables { enable_langfuse_storage = false }
  assert {
    condition     = length(aws_eks_pod_identity_association.langfuse) == 0
    error_message = "Explicitly disabled management inputs must not associate a new service account."
  }
}
run "langfuse_scoped_association" {
  command = apply
  variables { enable_langfuse_storage = true }
  assert {
    condition     = aws_eks_pod_identity_association.langfuse[0].namespace == "langfuse" && aws_eks_pod_identity_association.langfuse[0].service_account == "langfuse" && aws_eks_pod_identity_association.langfuse[0].role_arn == "arn:aws:iam::123456789012:role/langfuse"
    error_message = "Only the dedicated Langfuse service account receives the prepared storage role."
  }
}
