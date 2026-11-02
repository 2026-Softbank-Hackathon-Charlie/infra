mock_provider "aws" {
  mock_data "aws_ssm_parameter" { defaults = { value = "ami-0123456789abcdef0" } }
  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::123456789012:role/mock-build-role"
    }
  }
}
variables {
  aws_account_id = "123456789012"
}
run "loki_storage_boundary" {
  command = apply

  assert {
    condition     = aws_s3_bucket.loki.bucket == "charlie-dev-loki-123456789012-ap-northeast-2" && !coalesce(aws_s3_bucket.loki.force_destroy, false)
    error_message = "Loki bucket name must match the CI policy and never be force-destroyed with logs inside."
  }

  assert {
    condition = jsonencode(jsondecode(aws_iam_role.loki.assume_role_policy).Statement[0].Condition.StringEquals) == jsonencode({
      "aws:RequestTag/eks-cluster-name"           = "charlie-dev-management"
      "aws:RequestTag/kubernetes-namespace"       = "observability"
      "aws:RequestTag/kubernetes-service-account" = "loki"
    })
    error_message = "Only the management observability/loki service account may assume the Loki role."
  }

  assert {
    condition = toset(flatten([
      for statement in jsondecode(aws_iam_role_policy.loki.policy).Statement : statement.Resource
    ])) == toset([aws_s3_bucket.loki.arn, "${aws_s3_bucket.loki.arn}/*"])
    error_message = "Loki may touch only its own bucket."
  }
}
