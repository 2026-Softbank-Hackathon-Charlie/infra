# Image scan (platform SEC-04, ADR 0065): the platform repository's hourly
# scan-images workflow reads GKE app images to scan them with Grype. GitHub
# OIDC, no key: only that workflow on main may impersonate the scanner, and
# the scanner may only read the app image repositories.

locals {
  scan_workflow    = "/.github/workflows/scan-images.yml@refs/heads/main"
  scan_read_repos  = toset(concat([google_artifact_registry_repository.apps.repository_id], var.image_scan_extra_repositories))
  scan_pool_member = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.image_scan.name}/attribute.repository_id/${var.platform_repository_id}"
}

# Its own federation boundary: GitHub tokens, not the CodeBuild pool's AWS sessions.
resource "google_iam_workload_identity_pool" "image_scan" {
  project                   = var.project_id
  workload_identity_pool_id = "charlie-image-scan"
  display_name              = "Charlie image scan"
  description               = "The platform repository's scan-images workflow on main; read only."
}

resource "google_iam_workload_identity_pool_provider" "image_scan" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.image_scan.workload_identity_pool_id
  workload_identity_pool_provider_id = "platform-main"
  attribute_mapping = {
    "google.subject"          = "assertion.sub"
    "attribute.repository_id" = "assertion.repository_id"
  }
  attribute_condition = "assertion.repository_id == '${var.platform_repository_id}' && assertion.repository_owner_id == '${var.github_owner_id}' && assertion.ref == 'refs/heads/main' && assertion.workflow_ref.endsWith('${local.scan_workflow}')"
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "image_scanner" {
  project      = var.project_id
  account_id   = "charlie-image-scanner"
  display_name = "HopCharlie image scanner"
  description  = "Reads app images for the platform scan-images workflow (Grype). No push, nothing project-wide."
}

resource "google_service_account_iam_member" "image_scanner" {
  service_account_id = google_service_account.image_scanner.name
  role               = "roles/iam.workloadIdentityUser"
  member             = local.scan_pool_member
}

# Read only, per repository.
resource "google_artifact_registry_repository_iam_member" "image_scanner" {
  for_each   = local.scan_read_repos
  project    = var.project_id
  location   = var.region
  repository = each.value
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.image_scanner.email}"
}
