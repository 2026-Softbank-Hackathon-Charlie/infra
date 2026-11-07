data "google_project" "target" {
  project_id = var.project_id
}

locals {
  subject        = "system:serviceaccount:${var.platform_namespace}:${var.platform_service_account}"
  api_subject    = "system:serviceaccount:${var.platform_namespace}:hopcharlie-platform-api"
  provider_name  = "projects/${data.google_project.target.number}/locations/global/workloadIdentityPools/${var.workload_identity_pool_id}/providers/${var.workload_identity_provider_id}"
  token_audience = "https://iam.googleapis.com/${local.provider_name}"
  member         = "serviceAccount:${google_service_account.platform.email}"
  # App buckets and their objects only. Cloud Storage resource names use "_"
  # in place of the project.
  bucket_condition = "resource.name.startsWith(\"projects/_/buckets/${var.bucket_prefix}\")"

  # What the gcs driver does to an app bucket (platform ADR 0016): make it,
  # label it, grant the app's service account on it, empty it and delete it.
  # Also read and write its objects: rclone (remote gke:) moves registered
  # apps' files with this identity (platform PLATFORM_APP_FILES, ADR 0064).
  bucket_permissions = [
    "storage.buckets.create",
    "storage.buckets.delete",
    "storage.buckets.get",
    "storage.buckets.getIamPolicy",
    "storage.buckets.setIamPolicy",
    "storage.buckets.update",
    "storage.objects.create",
    "storage.objects.delete",
    "storage.objects.get",
    "storage.objects.list",
    "storage.objects.update",
  ]
  # Identity operations and bucket discovery need project-level permissions.
  # Listing proves project ownership in the gcs driver (ADR 0049); mutations
  # still use the prefix-conditioned bucket role above.
  identity_permissions = [
    "storage.buckets.list",
    "iam.serviceAccounts.create",
    "iam.serviceAccounts.delete",
    "iam.serviceAccounts.get",
    "iam.serviceAccounts.list",
    "storage.hmacKeys.create",
    "storage.hmacKeys.delete",
    "storage.hmacKeys.get",
    "storage.hmacKeys.list",
    "storage.hmacKeys.update",
  ]
}

# A federation boundary separate from Argo CD (ADR 0021), trusting one subject.
resource "google_iam_workload_identity_pool" "platform" {
  project                   = var.project_id
  workload_identity_pool_id = var.workload_identity_pool_id
  display_name              = "Charlie HopCharlie platform"
  description               = "Management EKS platform API; not Argo CD or GitHub Actions."
}

resource "google_iam_workload_identity_pool_provider" "platform" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.platform.workload_identity_pool_id
  workload_identity_pool_provider_id = var.workload_identity_provider_id
  attribute_mapping                  = { "google.subject" = "assertion.sub" }
  attribute_condition                = var.onprem_registration_enabled ? "assertion.sub in ${jsonencode([local.subject, local.api_subject])}" : "assertion.sub == \"${local.subject}\""
  oidc {
    issuer_uri        = var.eks_oidc_issuer_url
    allowed_audiences = [local.token_audience]
  }
}

resource "google_service_account" "platform" {
  project      = var.project_id
  account_id   = var.service_account_id
  display_name = "HopCharlie platform app storage"
  description  = "Makes GKE app buckets, their service accounts and HMAC keys (infra ADR 0041)."
}

resource "google_service_account_iam_member" "platform" {
  service_account_id = google_service_account.platform.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principal://iam.googleapis.com/${google_iam_workload_identity_pool.platform.name}/subject/${local.subject}"
}

resource "google_service_account_iam_member" "platform_api" {
  count              = var.onprem_registration_enabled ? 1 : 0
  service_account_id = google_service_account.platform.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principal://iam.googleapis.com/${google_iam_workload_identity_pool.platform.name}/subject/${local.api_subject}"
}

resource "google_project_iam_custom_role" "buckets" {
  project     = var.project_id
  role_id     = "charliePlatformAppBuckets"
  title       = "HopCharlie platform app buckets"
  description = "App bucket lifecycle; granted only on ${var.bucket_prefix}* buckets."
  permissions = local.bucket_permissions
}

resource "google_project_iam_custom_role" "identities" {
  project     = var.project_id
  role_id     = "charliePlatformAppIdentities"
  title       = "HopCharlie platform app identities"
  description = "App service accounts, HMAC keys and bucket discovery, project-wide (infra ADR 0049)."
  permissions = local.identity_permissions
}

resource "google_project_iam_member" "buckets" {
  project = var.project_id
  role    = google_project_iam_custom_role.buckets.id
  member  = local.member
  condition {
    title       = "app-buckets-only"
    description = "Only buckets named ${var.bucket_prefix}* and their objects."
    expression  = local.bucket_condition
  }
}

resource "google_project_iam_member" "identities" {
  project = var.project_id
  role    = google_project_iam_custom_role.identities.id
  member  = local.member
}
