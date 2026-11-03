locals {
  apis = toset([
    "serviceusage.googleapis.com", "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com", "iamcredentials.googleapis.com", "sts.googleapis.com",
    "compute.googleapis.com", "container.googleapis.com", "artifactregistry.googleapis.com",
    "storage.googleapis.com", "logging.googleapis.com", "monitoring.googleapis.com"
  ])
}
resource "google_project_service" "required" {
  for_each           = local.apis
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}
resource "google_artifact_registry_repository" "apps" {
  project       = var.project_id
  location      = var.region
  repository_id = "charlie-apps"
  description   = "Application images for Charlie GKE workloads"
  format        = "DOCKER"
  mode          = "STANDARD_REPOSITORY"
  depends_on    = [google_project_service.required]
}
resource "google_storage_bucket" "state" {
  name                        = var.state_bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  versioning { enabled = true }
  depends_on = [google_project_service.required]
}
resource "google_service_account" "deploy" {
  account_id   = "charlie-github-terraform"
  display_name = "Charlie GitHub Terraform runtime deployer"
  depends_on   = [google_project_service.required]
}
resource "google_service_account" "nodes" {
  account_id   = "charlie-gke-nodes"
  display_name = "Charlie GKE nodes"
  depends_on   = [google_project_service.required]
}
resource "google_project_iam_member" "deploy" {
  for_each = toset(["roles/container.admin", "roles/compute.networkAdmin"])
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.deploy.email}"
}
resource "google_project_iam_member" "nodes" {
  for_each = toset(["roles/container.defaultNodeServiceAccount", "roles/artifactregistry.reader"])
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.nodes.email}"
}
resource "google_service_account_iam_member" "node_user" {
  service_account_id = google_service_account.nodes.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deploy.email}"
}
resource "google_storage_bucket_iam_member" "state" {
  bucket = google_storage_bucket.state.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.deploy.email}"
}
resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "charlie-github"
  display_name              = "Charlie GitHub Actions"
  depends_on                = [google_project_service.required]
}
resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "infra"
  attribute_mapping = {
    "google.subject"                = "assertion.sub"
    "attribute.repository_id"       = "assertion.repository_id"
    "attribute.repository_owner_id" = "assertion.repository_owner_id"
  }
  attribute_condition = "assertion.repository_id == '${var.github_repository_id}' && assertion.repository_owner_id == '${var.github_repository_owner_id}' && assertion.ref == '${var.github_ref}'"
  oidc { issuer_uri = "https://token.actions.githubusercontent.com" }
}
resource "google_service_account_iam_member" "github" {
  service_account_id = google_service_account.deploy.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository_id/${var.github_repository_id}"
}
