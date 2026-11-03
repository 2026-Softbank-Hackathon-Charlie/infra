output "ecr_repository_url" {
  value = aws_ecr_repository.platform.repository_url
}

output "console_repository_url" {
  value = aws_ecr_repository.console.repository_url
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
  description = "AWS_DEPLOY_ROLE_ARN of the image publish workflows, by repository (platform, frontend)."
  value       = { for repo, role in aws_iam_role.image_publish : repo => role.arn }
}
