mock_provider "google" {}

override_resource {
  target          = google_service_account.cnpg_poc_backup
  override_during = plan
  values = {
    name  = "projects/charlie-test-project/serviceAccounts/charlie-cnpg-poc-backup@charlie-test-project.iam.gserviceaccount.com"
    email = "charlie-cnpg-poc-backup@charlie-test-project.iam.gserviceaccount.com"
  }
}

variables {
  project_id                 = "charlie-test-project"
  state_bucket_name          = "charlie-test-state"
  github_repository_id       = "123456789"
  github_repository_owner_id = "987654321"
}

run "backup_is_disabled_by_default" {
  command = plan
  assert {
    condition     = length(google_storage_bucket.cnpg_poc_backup) == 0 && length(google_service_account.cnpg_poc_backup) == 0 && length(google_service_account_iam_member.cnpg_poc_backup) == 0 && length(google_storage_bucket_iam_member.cnpg_poc_backup) == 0
    error_message = "Existing bootstrap must not create a CNPG bucket, identity or grants without explicit opt-in."
  }
  assert {
    condition     = output.cnpg_poc_backup_bucket_name == null && output.cnpg_poc_backup_destination_path == null && output.cnpg_poc_backup_service_account_email == null
    error_message = "Disabled backup must not expose an incomplete connection configuration."
  }
}

run "backup_scopes_native_identity_and_bucket_access" {
  command = plan
  variables {
    enable_cnpg_poc_backup      = true
    cnpg_poc_backup_bucket_name = "charlie-test-cnpg-poc-backup"
  }
  assert {
    condition     = length(google_storage_bucket.cnpg_poc_backup) == 1 && length(google_service_account.cnpg_poc_backup) == 1 && length(google_service_account_iam_member.cnpg_poc_backup) == 1 && length(google_storage_bucket_iam_member.cnpg_poc_backup) == 2
    error_message = "Opt-in backup must add exactly one bucket, one identity and three additive identity/bucket grants."
  }
  assert {
    condition     = google_storage_bucket.cnpg_poc_backup[0].name == "charlie-test-cnpg-poc-backup" && google_storage_bucket.cnpg_poc_backup[0].location == var.region && google_storage_bucket.cnpg_poc_backup[0].uniform_bucket_level_access && google_storage_bucket.cnpg_poc_backup[0].public_access_prevention == "enforced" && !google_storage_bucket.cnpg_poc_backup[0].force_destroy && length(google_storage_bucket.cnpg_poc_backup[0].lifecycle_rule) == 0
    error_message = "Barman's dedicated regional bucket must be private without forced deletion or an independent expiration policy."
  }
  assert {
    condition     = google_service_account.cnpg_poc_backup[0].project == var.project_id && google_service_account.cnpg_poc_backup[0].account_id == "charlie-cnpg-poc-backup" && google_service_account_iam_member.cnpg_poc_backup[0].service_account_id == google_service_account.cnpg_poc_backup[0].name && google_service_account_iam_member.cnpg_poc_backup[0].role == "roles/iam.workloadIdentityUser" && google_service_account_iam_member.cnpg_poc_backup[0].member == "serviceAccount:charlie-test-project.svc.id.goog[charlie-db/postgres-poc]"
    error_message = "Only the charlie-db/postgres-poc Kubernetes identity may impersonate the dedicated backup identity without a key."
  }
  assert {
    condition = (
      toset(keys(google_storage_bucket_iam_member.cnpg_poc_backup)) == toset(["roles/storage.objectAdmin", "roles/storage.legacyBucketReader"]) &&
      alltrue([for role, grant in google_storage_bucket_iam_member.cnpg_poc_backup :
        grant.role == role && grant.bucket == "charlie-test-cnpg-poc-backup" && grant.member == "serviceAccount:charlie-cnpg-poc-backup@charlie-test-project.iam.gserviceaccount.com"
      ])
    )
    error_message = "Object operations and bucket metadata reads must remain additive grants on this backup bucket only."
  }
  assert {
    condition     = toset(keys(google_project_iam_member.deploy)) == toset(["roles/container.admin", "roles/compute.networkAdmin"]) && toset(keys(google_project_iam_member.nodes)) == toset(["roles/container.defaultNodeServiceAccount", "roles/artifactregistry.reader"])
    error_message = "Backup setup must not add project storage/IAM administration or node grants."
  }
  assert {
    condition     = output.cnpg_poc_backup_bucket_name == "charlie-test-cnpg-poc-backup" && output.cnpg_poc_backup_destination_path == "gs://charlie-test-cnpg-poc-backup/source/" && output.cnpg_poc_backup_service_account_email == "charlie-cnpg-poc-backup@charlie-test-project.iam.gserviceaccount.com"
    error_message = "Expose only the public bucket, Barman source prefix and workload identity email for the CNPG chart."
  }
}

run "enabled_backup_requires_bucket_name" {
  command = plan
  variables { enable_cnpg_poc_backup = true }
  expect_failures = [var.cnpg_poc_backup_bucket_name]
}

run "reject_invalid_backup_bucket_name" {
  command = plan
  variables { cnpg_poc_backup_bucket_name = "INVALID/Bucket" }
  expect_failures = [var.cnpg_poc_backup_bucket_name]
}
