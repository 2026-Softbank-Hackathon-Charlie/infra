mock_provider "aws" {
  mock_data "aws_ssm_parameter" { defaults = { value = "ami-0123456789abcdef0" } }
  mock_resource "aws_s3_bucket" { defaults = { arn = "arn:aws:s3:::mock-langfuse" } }
  mock_resource "aws_iam_role" { defaults = { arn = "arn:aws:iam::123456789012:role/mock-role" } }
}
variables { aws_account_id = "123456789012" }
run "disabled" {
  command = plan
  variables { enable_langfuse_storage = false }
  assert {
    condition     = length(aws_s3_bucket.langfuse) == 0 && length(aws_iam_role.langfuse) == 0 && output.langfuse_storage == null
    error_message = "Explicitly disabled inputs must not create Langfuse cloud resources."
  }
}
run "event_boundary" {
  command = apply
  variables { enable_langfuse_storage = true }
  assert {
    condition     = aws_s3_bucket.langfuse[0].bucket == "charlie-dev-langfuse-123456789012-ap-northeast-2" && !aws_s3_bucket.langfuse[0].force_destroy
    error_message = "Langfuse uses a dedicated retained bucket."
  }
  assert {
    condition = jsonencode(jsondecode(aws_iam_role.langfuse[0].assume_role_policy).Statement[0].Condition.StringEquals) == jsonencode({
      "aws:RequestTag/eks-cluster-name"           = "charlie-dev-management"
      "aws:RequestTag/kubernetes-namespace"       = "langfuse"
      "aws:RequestTag/kubernetes-service-account" = "langfuse"
    })
    error_message = "Only the management Langfuse SA may assume this role."
  }
  assert {
    condition     = jsondecode(aws_iam_role_policy.langfuse[0].policy).Statement[1].Resource[0] == "${aws_s3_bucket.langfuse[0].arn}/events/*" && toset(jsondecode(aws_iam_role_policy.langfuse[0].policy).Statement[1].Action) == toset(["s3:GetObject", "s3:PutObject"])
    error_message = "Only event objects may be read/written; no deletion or other bucket access."
  }
  assert {
    condition     = jsondecode(aws_iam_role_policy.langfuse[0].policy).Statement[0].Condition.StringLike["s3:prefix"][1] == "events/*" && aws_s3_bucket_public_access_block.langfuse[0].block_public_policy && aws_s3_bucket_public_access_block.langfuse[0].restrict_public_buckets
    error_message = "Listing is prefix-scoped and public access is blocked."
  }
}
