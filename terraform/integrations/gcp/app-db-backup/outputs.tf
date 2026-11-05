output "bucket_name" {
  value = google_storage_bucket.backups.name
}
output "service_account_email" {
  description = "Annotate each Cluster's serviceAccountTemplate with iam.gke.io/gcp-service-account set to this."
  value       = google_service_account.backups.email
}
