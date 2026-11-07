output "state_bucket_name" { value = google_storage_bucket.state.name }
output "workload_identity_provider" { value = google_iam_workload_identity_pool_provider.github.name }
output "deploy_service_account_email" { value = google_service_account.deploy.email }
output "node_service_account_email" { value = google_service_account.nodes.email }

output "cnpg_poc_backup_bucket_name" {
  description = "Dedicated native GCS bucket for the optional CNPG PoC backup."
  value       = var.enable_cnpg_poc_backup ? google_storage_bucket.cnpg_poc_backup[0].name : null
}

output "cnpg_poc_backup_destination_path" {
  description = "Barman destinationPath for the optional CNPG PoC source backup."
  value       = var.enable_cnpg_poc_backup ? "gs://${google_storage_bucket.cnpg_poc_backup[0].name}/source/" : null
}

output "cnpg_poc_backup_service_account_email" {
  description = "GSA annotation for the charlie-db/postgres-poc Kubernetes service account."
  value       = var.enable_cnpg_poc_backup ? google_service_account.cnpg_poc_backup[0].email : null
}
