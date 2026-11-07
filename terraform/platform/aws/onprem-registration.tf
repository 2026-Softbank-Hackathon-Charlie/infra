# Opt-in identity split. No OAuth credential is generated, read or stored by
# Terraform: an approved operator provisions the separate SecureString later.
variable "onprem_registration_enabled" {
  type        = bool
  default     = false
  description = "Prepare API-only Pod Identity and exclude its dedicated onprem OAuth path from the legacy shared identity. Enable only after plan approval."
}

locals {
  registration_parameter_arn = "arn:aws:ssm:${var.region}:${data.aws_caller_identity.me.account_id}:parameter${local.prefix}/onprem-registration-oauth"
  api_existing_policies = {
    security   = local.security_read_policy
    parameters = local.own_parameters_policy
    storage    = aws_iam_role_policy.platform_app_storage.policy
    build      = aws_iam_role_policy.platform_app_build.policy
  }
}

resource "aws_iam_role" "platform_api" {
  count              = var.onprem_registration_enabled ? 1 : 0
  name               = "${local.name}-platform-api"
  assume_role_policy = aws_iam_role.platform_pod.assume_role_policy
}

resource "aws_iam_role_policy" "platform_api_existing" {
  for_each = var.onprem_registration_enabled ? local.api_existing_policies : {}
  name     = "api-${each.key}"
  role     = aws_iam_role.platform_api[0].id
  policy   = each.value
}

resource "aws_iam_role_policy_attachment" "platform_api_onboarding" {
  count      = var.onprem_registration_enabled ? 1 : 0
  role       = aws_iam_role.platform_api[0].name
  policy_arn = aws_iam_policy.platform_onboarding.arn
}

resource "aws_iam_role_policy" "platform_api_registration_oauth" {
  count = var.onprem_registration_enabled ? 1 : 0
  name  = "read-onprem-registration-oauth"
  role  = aws_iam_role.platform_api[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "ssm:GetParameter"
      Resource = local.registration_parameter_arn
    }]
  })
}

# The old SA is still used by gateway/Postgres. Its old wildcard parameter
# policy must not let these pods obtain the new registration OAuth credential.
resource "aws_iam_role_policy" "platform_legacy_deny_registration_oauth" {
  count = var.onprem_registration_enabled ? 1 : 0
  name  = "deny-onprem-registration-oauth"
  role  = aws_iam_role.platform_pod.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Deny"
        Action   = "ssm:GetParameter*"
        Resource = local.registration_parameter_arn
      },
      {
        # A child-only deny does not block a recursive ancestor path query.
        # The old fetch script enumerates only these two paths. Deny all
        # other path queries, including / and /hopcharlie, on this identity.
        Effect = "Deny"
        Action = "ssm:GetParametersByPath"
        NotResource = [
          "arn:aws:ssm:${var.region}:${data.aws_caller_identity.me.account_id}:parameter${local.prefix}/kube",
          "arn:aws:ssm:${var.region}:${data.aws_caller_identity.me.account_id}:parameter${local.prefix}/kube/*",
          "arn:aws:ssm:${var.region}:${data.aws_caller_identity.me.account_id}:parameter${local.prefix}/sealed-secrets",
          "arn:aws:ssm:${var.region}:${data.aws_caller_identity.me.account_id}:parameter${local.prefix}/sealed-secrets/*",
        ]
      },
    ]
  })
}

resource "aws_eks_pod_identity_association" "platform_api" {
  count           = var.onprem_registration_enabled ? 1 : 0
  cluster_name    = var.management_cluster
  namespace       = "charlie-platform"
  service_account = "hopcharlie-platform-api"
  role_arn        = aws_iam_role.platform_api[0].arn
}
