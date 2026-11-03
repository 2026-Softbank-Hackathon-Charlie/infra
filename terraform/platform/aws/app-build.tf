locals {
  app_build_name        = "hopcharlie-app-build"
  app_build_project_arn = "arn:aws:codebuild:${var.region}:${data.aws_caller_identity.me.account_id}:project/${local.app_build_name}"
  app_repository_arn    = "arn:aws:ecr:${var.region}:${data.aws_caller_identity.me.account_id}:repository/hopcharlie/apps/*"
}

resource "aws_cloudwatch_log_group" "app_build" {
  name              = "/aws/codebuild/${local.app_build_name}"
  retention_in_days = 3
}

resource "aws_iam_role" "app_build" {
  name = local.app_build_name
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "codebuild.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.me.account_id }
        ArnEquals    = { "aws:SourceArn" = local.app_build_project_arn }
      }
    }]
  })
}

resource "aws_iam_role_policy" "app_build" {
  name = "app-build-ecr-and-logs"
  role = aws_iam_role.app_build.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ecr:CreateRepository", "ecr:DescribeRepositories", "ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:BatchGetImage"]
        Resource = local.app_repository_arn
      },
      { Effect = "Allow", Action = "ecr:GetAuthorizationToken", Resource = "*" },
      { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "arn:aws:logs:${var.region}:${data.aws_caller_identity.me.account_id}:log-group:${aws_cloudwatch_log_group.app_build.name}:*" },
    ]
  })
}

resource "aws_codebuild_project" "app_build" {
  name                   = local.app_build_name
  service_role           = aws_iam_role.app_build.arn
  build_timeout          = 15
  queued_timeout         = 15
  concurrent_build_limit = 1
  depends_on             = [aws_iam_role_policy.app_build]
  artifacts { type = "NO_ARTIFACTS" }
  environment {
    compute_type                = "BUILD_GENERAL1_SMALL"
    image                       = "aws/codebuild/standard:7.0"
    type                        = "LINUX_CONTAINER"
    privileged_mode             = true
    image_pull_credentials_type = "CODEBUILD"
  }
  source {
    type      = "NO_SOURCE"
    buildspec = <<-YAML
      version: 0.2
      phases:
        build:
          commands:
            - echo "A per-build buildspec override is required"
            - exit 1
    YAML
  }
  logs_config {
    cloudwatch_logs {
      group_name = aws_cloudwatch_log_group.app_build.name
    }
  }
}

resource "aws_iam_role_policy" "platform_app_build" {
  name = "app-build-access"
  role = aws_iam_role.platform_pod.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["codebuild:StartBuild", "codebuild:BatchGetBuilds"], Resource = local.app_build_project_arn },
      { Effect = "Allow", Action = "logs:GetLogEvents", Resource = "arn:aws:logs:${var.region}:${data.aws_caller_identity.me.account_id}:log-group:${aws_cloudwatch_log_group.app_build.name}:*" },
    ]
  })
}
