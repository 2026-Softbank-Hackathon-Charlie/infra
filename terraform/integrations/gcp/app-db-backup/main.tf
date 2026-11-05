# Backups of the app databases on GKE (infra ADR 0057, platform ADR 0025):
# one private bucket and one Google service account that only it may use. The
# CNPG Barman Cloud plugin authenticates with GKE Workload Identity as the
# Kubernetes service account CNPG makes for each Cluster, as charlie-db's
# PoC backup does (ADR 0031). No key is made.
resource "google_storage_bucket" "backups" {
  project                     = var.project_id
  name                        = var.bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  # Barman owns backup and WAL retention; no lifecycle rule deletes objects.
  lifecycle {
    prevent_destroy = true
  }
}

resource "google_service_account" "backups" {
  project      = var.project_id
  account_id   = var.service_account_id
  display_name = "Charlie app database backups on GKE"
}

resource "google_service_account_iam_member" "clusters" {
  for_each           = toset(var.clusters)
  service_account_id = google_service_account.backups.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[${each.value}]"
}

resource "google_storage_bucket_iam_member" "backups" {
  for_each = toset([
    "roles/storage.objectAdmin",
    # Native GCS backup clients read bucket metadata before uploading.
    "roles/storage.legacyBucketReader",
  ])
  bucket = google_storage_bucket.backups.name
  role   = each.value
  member = "serviceAccount:${google_service_account.backups.email}"
}
