output "state_bucket_name" { value = google_storage_bucket.state.name }
output "workload_identity_provider" { value = google_iam_workload_identity_pool_provider.github.name }
output "deploy_service_account_email" { value = google_service_account.deploy.email }
output "node_service_account_email" { value = google_service_account.nodes.email }
output "app_image_repository_id" { value = google_artifact_registry_repository.apps.id }
output "app_image_repository_prefix" { value = "${google_artifact_registry_repository.apps.location}-docker.pkg.dev/${google_artifact_registry_repository.apps.project}/${google_artifact_registry_repository.apps.repository_id}" }
