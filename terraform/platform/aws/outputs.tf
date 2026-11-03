output "ecr_repository_url" {
  value = aws_ecr_repository.platform.repository_url
}

output "console_repository_url" {
  value = aws_ecr_repository.console.repository_url
}

output "moimroom_image_repository_name" {
  description = "AWS_ECR_REPOSITORY for the moimroom image publishing workflow."
  value       = aws_ecr_repository.moimroom.name
}

output "moimroom_image_repository_url" {
  description = "ECR repository URI for published moimroom images."
  value       = aws_ecr_repository.moimroom.repository_url
}

output "moimroom_github_image_publish_role_arn" {
  description = "AWS_IMAGE_PUBLISH_ROLE_ARN for the moimroom workflow on main."
  value       = aws_iam_role.image_publish["moimroom"].arn
}

output "operator_tokens" {
  description = "One operator token per name. Hand each person only their own."
  value       = { for n in var.operators : n => random_password.operator[n].result }
  sensitive   = true
}

output "planner_token" {
  description = "Agent token for the AI planner (can submit plans, cannot approve)."
  value       = random_password.planner.result
  sensitive   = true
}

output "viewer_token" {
  description = "Read-only token (demo screen, judges)."
  value       = random_password.viewer.result
  sensitive   = true
}

output "platform_pod_role_arn" {
  description = "AWS role of the platform's pods on the management EKS cluster (Pod Identity)."
  value       = aws_iam_role.platform_pod.arn
}

output "image_publish_role_arns" {
  description = "Image publishing role ARNs by repository (platform, frontend, moimroom)."
  value       = { for repo, role in aws_iam_role.image_publish : repo => role.arn }
}
