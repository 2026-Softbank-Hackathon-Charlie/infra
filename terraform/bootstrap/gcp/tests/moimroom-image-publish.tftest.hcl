mock_provider "google" {
  override_during = plan
  mock_resource "google_iam_workload_identity_pool" {
    defaults = { name = "projects/123456789012/locations/global/workloadIdentityPools/charlie-github" }
  }
}
override_resource {
  target          = google_service_account.deploy
  override_during = plan
  values = {
    name  = "projects/charlie-test-project/serviceAccounts/charlie-github-terraform@charlie-test-project.iam.gserviceaccount.com"
    email = "charlie-github-terraform@charlie-test-project.iam.gserviceaccount.com"
  }
}
override_resource {
  target          = google_service_account.nodes
  override_during = plan
  values = {
    name  = "projects/charlie-test-project/serviceAccounts/charlie-gke-nodes@charlie-test-project.iam.gserviceaccount.com"
    email = "charlie-gke-nodes@charlie-test-project.iam.gserviceaccount.com"
  }
}
override_resource {
  target          = google_service_account.moimroom_publisher
  override_during = plan
  values = {
    name  = "projects/charlie-test-project/serviceAccounts/charlie-moimroom-publisher@charlie-test-project.iam.gserviceaccount.com"
    email = "charlie-moimroom-publisher@charlie-test-project.iam.gserviceaccount.com"
  }
}
override_resource {
  target          = google_iam_workload_identity_pool_provider.moimroom
  override_during = plan
  values = {
    name = "projects/123456789012/locations/global/workloadIdentityPools/charlie-github/providers/moimroom"
  }
}
variables {
  project_id                 = "charlie-test-project"
  state_bucket_name          = "charlie-test-state"
  github_repository_id       = "123456789"
  github_repository_owner_id = "987654321"
}

run "publishing_is_disabled_by_default" {
  command = plan
  assert {
    condition     = length(google_service_account.moimroom_publisher) == 0 && length(google_iam_workload_identity_pool_provider.moimroom) == 0 && length(google_service_account_iam_member.moimroom_github) == 0 && length(google_artifact_registry_repository_iam_member.moimroom_publisher) == 0
    error_message = "Default bootstrap must not create the optional publisher or any of its trust and writer grants."
  }
  assert {
    condition     = output.moimroom_image_workload_identity_provider == null && output.moimroom_image_publisher_service_account == null && output.moimroom_image_repository == null
    error_message = "Disabled publishing must not expose an incomplete publisher configuration."
  }
}

run "repository_id_alone_does_not_enable_publishing" {
  command = plan
  variables { moimroom_repository_id = "246810123" }
  assert {
    condition     = length(google_service_account.moimroom_publisher) == 0 && length(google_iam_workload_identity_pool_provider.moimroom) == 0 && length(google_service_account_iam_member.moimroom_github) == 0 && length(google_artifact_registry_repository_iam_member.moimroom_publisher) == 0
    error_message = "A repository ID alone must not enable image publication."
  }
}

run "publisher_reuses_pool_and_scopes_trust_and_writer" {
  command = plan
  variables {
    enable_moimroom_image_publishing = true
    moimroom_repository_id           = "246810123"
  }
  assert {
    condition     = length(google_service_account.moimroom_publisher) == 1 && length(google_iam_workload_identity_pool_provider.moimroom) == 1 && length(google_service_account_iam_member.moimroom_github) == 1 && length(google_artifact_registry_repository_iam_member.moimroom_publisher) == 1
    error_message = "Opt-in publishing must add exactly one account, provider, account trust member and repository writer member."
  }
  assert {
    condition     = google_service_account.moimroom_publisher[0].project == var.project_id && google_service_account.moimroom_publisher[0].account_id == "charlie-moimroom-publisher"
    error_message = "Use a dedicated publisher identity in the existing project."
  }
  assert {
    condition     = google_iam_workload_identity_pool_provider.moimroom[0].workload_identity_pool_id == google_iam_workload_identity_pool.github.workload_identity_pool_id && google_iam_workload_identity_pool_provider.moimroom[0].workload_identity_pool_provider_id == "moimroom"
    error_message = "Reuse the existing GitHub pool with a separate moimroom provider."
  }
  assert {
    condition     = google_iam_workload_identity_pool_provider.moimroom[0].attribute_condition == "assertion.repository_id == '246810123' && assertion.repository_owner_id == '987654321' && assertion.ref == 'refs/heads/main'"
    error_message = "Publisher federation must reject every other repository, owner and ref."
  }
  assert {
    condition     = google_iam_workload_identity_pool_provider.moimroom[0].oidc[0].issuer_uri == "https://token.actions.githubusercontent.com" && try(length(google_iam_workload_identity_pool_provider.moimroom[0].oidc[0].allowed_audiences), 0) == 0 && google_iam_workload_identity_pool_provider.moimroom[0].attribute_mapping == google_iam_workload_identity_pool_provider.github.attribute_mapping
    error_message = "Keep the GitHub issuer, actual claims and canonical audience validation."
  }
  assert {
    condition     = google_service_account_iam_member.moimroom_github[0].service_account_id == google_service_account.moimroom_publisher[0].name && google_service_account_iam_member.moimroom_github[0].role == "roles/iam.workloadIdentityUser" && google_service_account_iam_member.moimroom_github[0].member == "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository_id/246810123"
    error_message = "Only the moimroom repository principal set may impersonate the dedicated publisher."
  }
  assert {
    condition     = google_artifact_registry_repository_iam_member.moimroom_publisher[0].project == google_artifact_registry_repository.apps.project && google_artifact_registry_repository_iam_member.moimroom_publisher[0].location == google_artifact_registry_repository.apps.location && google_artifact_registry_repository_iam_member.moimroom_publisher[0].repository == google_artifact_registry_repository.apps.repository_id && google_artifact_registry_repository_iam_member.moimroom_publisher[0].role == "roles/artifactregistry.writer" && google_artifact_registry_repository_iam_member.moimroom_publisher[0].member == "serviceAccount:${google_service_account.moimroom_publisher[0].email}"
    error_message = "Grant additive writer access only to the existing application repository."
  }
  assert {
    condition     = google_iam_workload_identity_pool_provider.github.attribute_condition == "assertion.repository_id == '123456789' && assertion.repository_owner_id == '987654321' && assertion.ref == 'refs/heads/main'" && google_service_account_iam_member.github.member == "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository_id/123456789" && google_service_account_iam_member.github.service_account_id == google_service_account.deploy.name
    error_message = "Enabling publishing must not widen or repoint the infra provider or infra deploy identity."
  }
  assert {
    condition     = toset(keys(google_project_iam_member.deploy)) == toset(["roles/container.admin", "roles/compute.networkAdmin"]) && toset(keys(google_project_iam_member.nodes)) == toset(["roles/container.defaultNodeServiceAccount", "roles/artifactregistry.reader"])
    error_message = "Publisher setup must not add project writer/editor privileges or alter GKE node grants."
  }
  assert {
    condition     = output.moimroom_image_workload_identity_provider == google_iam_workload_identity_pool_provider.moimroom[0].name && output.moimroom_image_publisher_service_account == google_service_account.moimroom_publisher[0].email && output.moimroom_image_repository == "asia-northeast3-docker.pkg.dev/charlie-test-project/charlie-apps/moimroom"
    error_message = "Expose the exact GitHub Actions identity and image repository variable values."
  }
}

run "publisher_main_is_independent_of_infra_branch_setting" {
  command = plan
  variables {
    enable_moimroom_image_publishing = true
    moimroom_repository_id           = "246810123"
    github_ref                       = "refs/heads/infra-release"
  }
  assert {
    condition     = google_iam_workload_identity_pool_provider.moimroom[0].attribute_condition == "assertion.repository_id == '246810123' && assertion.repository_owner_id == '987654321' && assertion.ref == 'refs/heads/main'" && endswith(google_iam_workload_identity_pool_provider.github.attribute_condition, "assertion.ref == 'refs/heads/infra-release'")
    error_message = "The optional publisher must stay on main even when the separate infra branch is configured differently."
  }
}

run "enabled_publishing_requires_repository_id" {
  command = plan
  variables { enable_moimroom_image_publishing = true }
  expect_failures = [var.moimroom_repository_id]
}

run "reject_publisher_repository_name" {
  command = plan
  variables { moimroom_repository_id = "owner/moimroom" }
  expect_failures = [var.moimroom_repository_id]
}

run "reject_publisher_condition_injection" {
  command = plan
  variables {
    enable_moimroom_image_publishing = true
    moimroom_repository_id           = "123' || true || '"
  }
  expect_failures = [var.moimroom_repository_id]
}
