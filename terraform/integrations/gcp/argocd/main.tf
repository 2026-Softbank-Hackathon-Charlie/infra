data "google_project" "target" {
  project_id = var.project_id
}

locals {
  subjects = toset([
    "system:serviceaccount:${var.argocd_namespace}:argocd-application-controller",
    "system:serviceaccount:${var.argocd_namespace}:argocd-server"
  ])
  provider_name  = "projects/${data.google_project.target.number}/locations/global/workloadIdentityPools/${var.workload_identity_pool_id}/providers/${var.workload_identity_provider_id}"
  token_audience = "https://iam.googleapis.com/${local.provider_name}"
}

resource "google_iam_workload_identity_pool" "argocd" {
  project                   = var.project_id
  workload_identity_pool_id = var.workload_identity_pool_id
  display_name              = "Charlie management Argo CD"
  description               = "Separate federation boundary for management EKS Argo CD; not GitHub Actions."
}

resource "google_iam_workload_identity_pool_provider" "argocd" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.argocd.workload_identity_pool_id
  workload_identity_pool_provider_id = var.workload_identity_provider_id
  attribute_mapping                  = { "google.subject" = "assertion.sub" }
  attribute_condition                = "assertion.sub in ${jsonencode(sort(tolist(local.subjects)))}"
  oidc {
    issuer_uri        = var.eks_oidc_issuer_url
    allowed_audiences = [local.token_audience]
  }
}

resource "google_service_account" "argocd" {
  project      = var.project_id
  account_id   = var.service_account_id
  display_name = "Charlie Argo CD GKE access"
}

# Cluster discovery is Google IAM; deployment rights remain explicit GKE RBAC.
resource "google_project_iam_member" "gke_discovery" {
  project = var.project_id
  role    = "roles/container.clusterViewer"
  member  = "serviceAccount:${google_service_account.argocd.email}"
}

resource "google_service_account_iam_member" "argocd" {
  for_each           = local.subjects
  service_account_id = google_service_account.argocd.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principal://iam.googleapis.com/${google_iam_workload_identity_pool.argocd.name}/subject/${each.value}"
}
