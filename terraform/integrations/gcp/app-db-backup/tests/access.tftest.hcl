mock_provider "google" {
  override_during = plan
  mock_resource "google_service_account" {
    defaults = {
      name  = "projects/charlie-test-project/serviceAccounts/charlie-app-db-backup@charlie-test-project.iam.gserviceaccount.com"
      email = "charlie-app-db-backup@charlie-test-project.iam.gserviceaccount.com"
    }
  }
}
variables {
  project_id  = "charlie-test-project"
  bucket_name = "charlie-test-app-db-backup"
}
run "private_bucket_in_the_region" {
  command = plan
  assert {
    condition     = google_storage_bucket.backups.location == "asia-northeast3" && google_storage_bucket.backups.uniform_bucket_level_access && google_storage_bucket.backups.public_access_prevention == "enforced" && !google_storage_bucket.backups.force_destroy
    error_message = "Keep the bucket private, in the GKE region, and never force-destroyed."
  }
  assert {
    condition     = length(google_storage_bucket.backups.lifecycle_rule) == 0
    error_message = "Barman owns retention; no lifecycle rule may delete backups."
  }
}
run "only_the_named_clusters_act_as_the_account" {
  command = plan
  assert {
    condition     = [for m in google_service_account_iam_member.clusters : m.member] == ["serviceAccount:charlie-test-project.svc.id.goog[charlie-apps/moimroom-db-gke]"]
    error_message = "Trust exactly the Kubernetes service account of each named Cluster."
  }
  assert {
    condition     = alltrue([for m in google_service_account_iam_member.clusters : m.role == "roles/iam.workloadIdentityUser"])
    error_message = "Grant Workload Identity use only."
  }
}
run "objects_of_its_bucket_only" {
  command = plan
  assert {
    condition     = toset([for m in google_storage_bucket_iam_member.backups : m.role]) == toset(["roles/storage.objectAdmin", "roles/storage.legacyBucketReader"]) && alltrue([for m in google_storage_bucket_iam_member.backups : m.bucket == "charlie-test-app-db-backup" && m.member == "serviceAccount:charlie-app-db-backup@charlie-test-project.iam.gserviceaccount.com"])
    error_message = "The account may use objects of this bucket only; no project role."
  }
}
run "refuses_wildcard_clusters" {
  command = plan
  variables {
    clusters = ["charlie-apps/*"]
  }
  expect_failures = [var.clusters]
}
