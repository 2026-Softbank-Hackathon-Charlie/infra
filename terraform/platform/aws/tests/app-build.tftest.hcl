mock_provider "aws" {
  mock_data "aws_caller_identity" { defaults = { account_id = "123456789012" } }
  mock_data "aws_subnets" { defaults = { ids = ["subnet-12345678"] } }
  mock_data "aws_ssm_parameter" { defaults = { insecure_value = "ami-12345678" } }
}
mock_provider "random" {}

run "app_build_contract" {
  command = plan
  assert {
    condition     = aws_codebuild_project.app_build.name == "hopcharlie-app-build" && aws_codebuild_project.app_build.build_timeout == 15 && aws_codebuild_project.app_build.queued_timeout == 15 && aws_codebuild_project.app_build.concurrent_build_limit == 3
    error_message = "Build limits and name must match the contract."
  }
  assert {
    condition     = aws_codebuild_project.app_build.cache[0].type == "LOCAL" && toset(aws_codebuild_project.app_build.cache[0].modes) == toset(["LOCAL_DOCKER_LAYER_CACHE"])
    error_message = "Builds must keep only the local Docker layer cache."
  }
  assert {
    condition     = aws_codebuild_project.app_build.source[0].type == "NO_SOURCE" && strcontains(aws_codebuild_project.app_build.source[0].buildspec, "exit 1") && aws_codebuild_project.app_build.artifacts[0].type == "NO_ARTIFACTS"
    error_message = "Builds require a buildspec override and no source or artifacts."
  }
  assert {
    condition     = aws_codebuild_project.app_build.environment[0].type == "LINUX_CONTAINER" && aws_codebuild_project.app_build.environment[0].image == "aws/codebuild/standard:7.0" && aws_codebuild_project.app_build.environment[0].compute_type == "BUILD_GENERAL1_SMALL" && aws_codebuild_project.app_build.environment[0].privileged_mode && aws_codebuild_project.app_build.environment[0].image_pull_credentials_type == "CODEBUILD"
    error_message = "Build environment must match the contract."
  }
  assert {
    condition     = aws_cloudwatch_log_group.app_build.name == "/aws/codebuild/hopcharlie-app-build" && aws_cloudwatch_log_group.app_build.retention_in_days == 3 && aws_codebuild_project.app_build.logs_config[0].cloudwatch_logs[0].group_name == aws_cloudwatch_log_group.app_build.name
    error_message = "Logs must use the dedicated three-day group."
  }
  assert {
    condition     = jsondecode(aws_iam_role.app_build.assume_role_policy).Statement[0].Principal.Service == "codebuild.amazonaws.com" && jsondecode(aws_iam_role.app_build.assume_role_policy).Statement[0].Action == "sts:AssumeRole" && jsondecode(aws_iam_role.app_build.assume_role_policy).Statement[0].Condition.StringEquals["aws:SourceAccount"] == "123456789012" && jsondecode(aws_iam_role.app_build.assume_role_policy).Statement[0].Condition.ArnEquals["aws:SourceArn"] == local.app_build_project_arn
    error_message = "Trust must be restricted to the account and project."
  }
  assert {
    condition     = length(jsondecode(aws_iam_role_policy.app_build.policy).Statement) == 3 && toset(jsondecode(aws_iam_role_policy.app_build.policy).Statement[0].Action) == toset(["ecr:CreateRepository", "ecr:DescribeRepositories", "ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:BatchGetImage"]) && jsondecode(aws_iam_role_policy.app_build.policy).Statement[0].Resource == "arn:aws:ecr:${var.region}:123456789012:repository/hopcharlie/apps/*" && jsondecode(aws_iam_role_policy.app_build.policy).Statement[1].Action == "ecr:GetAuthorizationToken" && jsondecode(aws_iam_role_policy.app_build.policy).Statement[1].Resource == "*"
    error_message = "ECR permissions must be separated by resource scope."
  }
  assert {
    condition     = toset(jsondecode(aws_iam_role_policy.app_build.policy).Statement[2].Action) == toset(["logs:CreateLogStream", "logs:PutLogEvents"]) && jsondecode(aws_iam_role_policy.app_build.policy).Statement[2].Resource == "arn:aws:logs:ap-northeast-2:123456789012:log-group:/aws/codebuild/hopcharlie-app-build:*"
    error_message = "Build log writes must target only the group's streams."
  }
  assert {
    condition     = length(jsondecode(aws_iam_role_policy.platform_app_build.policy).Statement) == 2 && toset(jsondecode(aws_iam_role_policy.platform_app_build.policy).Statement[0].Action) == toset(["codebuild:StartBuild", "codebuild:BatchGetBuilds"]) && jsondecode(aws_iam_role_policy.platform_app_build.policy).Statement[0].Resource == local.app_build_project_arn && jsondecode(aws_iam_role_policy.platform_app_build.policy).Statement[1].Action == "logs:GetLogEvents" && jsondecode(aws_iam_role_policy.platform_app_build.policy).Statement[1].Resource == "arn:aws:logs:ap-northeast-2:123456789012:log-group:/aws/codebuild/hopcharlie-app-build:*"
    error_message = "Platform permissions must target only the project and log streams."
  }
}
