output "state_bucket_name" { value = google_storage_bucket.state.name }
output "workload_identity_provider" { value = google_iam_workload_identity_pool_provider.github.name }
output "deploy_service_account_email" { value = google_service_account.deploy.email }
output "node_service_account_email" { value = google_service_account.nodes.email }
output "app_image_repository_id" { value = google_artifact_registry_repository.apps.id }
output "app_image_repository_prefix" { value = "${google_artifact_registry_repository.apps.location}-docker.pkg.dev/${google_artifact_registry_repository.apps.project}/${google_artifact_registry_repository.apps.repository_id}" }
output "moimroom_image_workload_identity_provider" {
  description = "GCP_WORKLOAD_IDENTITY_PROVIDER for the optional moimroom image publishing workflow."
  value       = var.enable_moimroom_image_publishing ? google_iam_workload_identity_pool_provider.moimroom[0].name : null
}
output "moimroom_image_publisher_service_account" {
  description = "GCP_IMAGE_PUBLISH_SERVICE_ACCOUNT for the optional moimroom image publishing workflow."
  value       = var.enable_moimroom_image_publishing ? google_service_account.moimroom_publisher[0].email : null
}
output "moimroom_image_repository" {
  description = "GCP_IMAGE_REPOSITORY for the optional moimroom image publishing workflow."
  value       = var.enable_moimroom_image_publishing ? "${google_artifact_registry_repository.apps.location}-docker.pkg.dev/${google_artifact_registry_repository.apps.project}/${google_artifact_registry_repository.apps.repository_id}/moimroom" : null
}
