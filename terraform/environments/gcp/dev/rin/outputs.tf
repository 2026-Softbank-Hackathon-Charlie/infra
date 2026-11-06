output "project_id" { value = var.project_id }
output "zone" { value = local.zone }
output "instance_name" { value = google_compute_instance.rin.name }
output "workload_identity_provider" { value = google_iam_workload_identity_pool_provider.rin.name }
output "deploy_service_account" { value = google_service_account.deploy.email }
