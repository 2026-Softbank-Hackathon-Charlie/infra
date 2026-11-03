mock_provider "google" {}
variables {
  project_id                 = "charlie-test-project"
  state_bucket_name          = "charlie-test-state"
  github_repository_id       = "123456789"
  github_repository_owner_id = "987654321"
}
run "state_and_identity_are_scoped" {
  command = plan
  assert {
    condition     = google_storage_bucket.state.uniform_bucket_level_access && google_storage_bucket.state.public_access_prevention == "enforced" && google_storage_bucket.state.versioning[0].enabled && !google_storage_bucket.state.force_destroy
    error_message = "State must be private, versioned, uniform access and cannot be force-destroyed."
  }
  assert {
    condition     = google_iam_workload_identity_pool_provider.github.attribute_condition == "assertion.repository_id == '123456789' && assertion.repository_owner_id == '987654321' && assertion.ref == 'refs/heads/main'"
    error_message = "Federation must require the exact immutable repo ID, owner ID and main branch."
  }
  assert {
    condition     = google_iam_workload_identity_pool_provider.github.oidc[0].issuer_uri == "https://token.actions.githubusercontent.com" && try(length(google_iam_workload_identity_pool_provider.github.oidc[0].allowed_audiences), 0) == 0 && google_iam_workload_identity_pool_provider.github.attribute_mapping["google.subject"] == "assertion.sub"
    error_message = "Use GitHub issuer and GCP's canonical provider audience validation; preserve the actual subject."
  }
  assert {
    condition     = toset(keys(google_project_iam_member.deploy)) == toset(["roles/container.admin", "roles/compute.networkAdmin"]) && google_service_account_iam_member.node_user.role == "roles/iam.serviceAccountUser" && google_storage_bucket_iam_member.state.role == "roles/storage.objectAdmin"
    error_message = "Deploy identity must not receive owner/editor, project IAM admin or project-wide storage admin."
  }
  assert {
    condition     = contains(keys(google_project_iam_member.nodes), "roles/container.defaultNodeServiceAccount") && contains(keys(google_project_service.required), "storage.googleapis.com") && alltrue([for api in google_project_service.required : !api.disable_on_destroy])
    error_message = "Enable required APIs without disabling them on teardown and grant recommended node permissions."
  }
}
run "app_repository_reuses_node_pull_identity" {
  command = plan
  assert {
    condition     = google_artifact_registry_repository.apps.project == var.project_id && google_artifact_registry_repository.apps.location == var.region && google_artifact_registry_repository.apps.repository_id == "charlie-apps" && google_artifact_registry_repository.apps.format == "DOCKER" && google_artifact_registry_repository.apps.mode == "STANDARD_REPOSITORY"
    error_message = "Application images must use the standard Docker repository in the workload project's region."
  }
  assert {
    condition     = output.app_image_repository_prefix == "asia-northeast3-docker.pkg.dev/charlie-test-project/charlie-apps"
    error_message = "Expose the regional Docker prefix for image publication and GKE deployment."
  }
  assert {
    condition     = toset(keys(google_project_iam_member.nodes)) == toset(["roles/container.defaultNodeServiceAccount", "roles/artifactregistry.reader"]) && google_project_iam_member.nodes["roles/artifactregistry.reader"].role == "roles/artifactregistry.reader"
    error_message = "Reuse the dedicated node's reader identity without adding image push permissions."
  }
}
run "reject_repository_name_instead_of_id" {
  command = plan
  variables { github_repository_id = "owner/repo" }
  expect_failures = [var.github_repository_id]
}
run "reject_owner_name_instead_of_id" {
  command = plan
  variables { github_repository_owner_id = "organization" }
  expect_failures = [var.github_repository_owner_id]
}
run "reject_pull_request_ref" {
  command = plan
  variables { github_ref = "refs/pull/1/merge" }
  expect_failures = [var.github_ref]
}
