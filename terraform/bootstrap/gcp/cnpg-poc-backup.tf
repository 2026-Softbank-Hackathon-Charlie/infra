resource "google_storage_bucket" "cnpg_poc_backup" {
  count                       = var.enable_cnpg_poc_backup ? 1 : 0
  project                     = var.project_id
  name                        = var.cnpg_poc_backup_bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  depends_on                  = [google_project_service.required]

  # Barman owns backup/WAL retention; do not expire objects independently.
  lifecycle {
    prevent_destroy = true
  }
}

resource "google_service_account" "cnpg_poc_backup" {
  count        = var.enable_cnpg_poc_backup ? 1 : 0
  project      = var.project_id
  account_id   = "charlie-cnpg-poc-backup"
  display_name = "Charlie CNPG PoC backup workload"
  depends_on   = [google_project_service.required]
}

resource "google_service_account_iam_member" "cnpg_poc_backup" {
  count              = var.enable_cnpg_poc_backup ? 1 : 0
  service_account_id = google_service_account.cnpg_poc_backup[0].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[charlie-db/postgres-poc]"
}

resource "google_storage_bucket_iam_member" "cnpg_poc_backup" {
  for_each = var.enable_cnpg_poc_backup ? toset([
    "roles/storage.objectAdmin",
    # Native GCS backup clients check bucket metadata before object uploads.
    "roles/storage.legacyBucketReader",
  ]) : toset([])
  bucket = google_storage_bucket.cnpg_poc_backup[0].name
  role   = each.value
  member = "serviceAccount:${google_service_account.cnpg_poc_backup[0].email}"
}
