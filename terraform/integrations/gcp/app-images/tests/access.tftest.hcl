mock_provider "google" {
  override_during = plan
  mock_resource "google_iam_workload_identity_pool" { defaults = { name = "projects/123456789012/locations/global/workloadIdentityPools/charlie-app-build" } }
  mock_resource "google_iam_workload_identity_pool_provider" { defaults = { name = "projects/123456789012/locations/global/workloadIdentityPools/charlie-app-build/providers/aws-codebuild" } }
  mock_resource "google_service_account" {
    defaults = {
      name  = "projects/charlie-test-project/serviceAccounts/charlie-app-image-publisher@charlie-test-project.iam.gserviceaccount.com"
      email = "charlie-app-image-publisher@charlie-test-project.iam.gserviceaccount.com"
    }
  }
}
variables {
  project_id             = "charlie-test-project"
  aws_account_id         = "111122223333"
  github_owner_id        = "350278309"
  platform_repository_id = "1442049765"
}
run "only_the_build_role_is_trusted" {
  command = plan
  assert {
    condition     = google_iam_workload_identity_pool_provider.build.aws[0].account_id == "111122223333" && google_iam_workload_identity_pool_provider.build.attribute_condition == "attribute.aws_role == \"arn:aws:sts::111122223333:assumed-role/hopcharlie-app-build\""
    error_message = "Trust only sessions of the CodeBuild role in the build account."
  }
  assert {
    condition     = google_service_account_iam_member.publisher.role == "roles/iam.workloadIdentityUser" && google_service_account_iam_member.publisher.member == "principalSet://iam.googleapis.com/projects/123456789012/locations/global/workloadIdentityPools/charlie-app-build/attribute.aws_role/arn:aws:sts::111122223333:assumed-role/hopcharlie-app-build"
    error_message = "Allow impersonation only for the build role."
  }
  assert {
    condition     = !contains(["charlie-argocd", "charlie-platform"], google_iam_workload_identity_pool.build.workload_identity_pool_id)
    error_message = "Keep the build pool separate from Argo CD's and the platform API's."
  }
}
run "push_only_on_its_own_repository" {
  command = plan
  assert {
    condition     = google_artifact_registry_repository.apps.repository_id == "hopcharlie-apps" && google_artifact_registry_repository.apps.location == "asia-northeast3" && google_artifact_registry_repository.apps.format == "DOCKER"
    error_message = "Use a separate Docker repository in the GKE region."
  }
  assert {
    condition     = google_artifact_registry_repository_iam_member.publisher.role == "roles/artifactregistry.writer" && google_artifact_registry_repository_iam_member.publisher.repository == "hopcharlie-apps"
    error_message = "The publisher may push to this repository only; no delete, no project role."
  }
  assert {
    condition     = output.image_prefix == "asia-northeast3-docker.pkg.dev/charlie-test-project/hopcharlie-apps"
    error_message = "GKE images live under the repository path."
  }
}
run "public_adc_uses_aws_session_and_impersonation" {
  command = plan
  assert {
    condition     = output.federation_config.subject_token_type == "urn:ietf:params:aws:token-type:aws4_request" && output.federation_config.credential_source.environment_id == "aws1" && output.federation_config.audience == "//iam.googleapis.com/projects/123456789012/locations/global/workloadIdentityPools/charlie-app-build/providers/aws-codebuild"
    error_message = "Use AWS session federation for this provider, not a stored key."
  }
  assert {
    condition     = output.federation_config.service_account_impersonation_url == "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/${output.publisher_service_account_email}:generateAccessToken"
    error_message = "ADC must impersonate only the publisher service account."
  }
}
run "reject_bad_account" {
  command = plan
  variables { aws_account_id = "*" }
  expect_failures = [var.aws_account_id]
}
run "reject_wildcard_role" {
  command = plan
  variables { aws_build_role_name = "*" }
  expect_failures = [var.aws_build_role_name]
}
run "only_the_platform_scan_workflow_on_main_reads_images" {
  command = plan
  assert {
    condition     = google_iam_workload_identity_pool_provider.image_scan.attribute_condition == "assertion.repository_id == '1442049765' && assertion.repository_owner_id == '350278309' && assertion.ref == 'refs/heads/main' && assertion.workflow_ref.endsWith('/.github/workflows/scan-images.yml@refs/heads/main')" && google_iam_workload_identity_pool_provider.image_scan.oidc[0].issuer_uri == "https://token.actions.githubusercontent.com"
    error_message = "Trust only the platform repository's scan-images workflow on main."
  }
  assert {
    condition     = google_iam_workload_identity_pool.image_scan.workload_identity_pool_id != google_iam_workload_identity_pool.build.workload_identity_pool_id
    error_message = "Keep the scanner's GitHub pool separate from the CodeBuild pool."
  }
  assert {
    condition     = google_service_account_iam_member.image_scanner.role == "roles/iam.workloadIdentityUser" && endswith(google_service_account_iam_member.image_scanner.member, "/attribute.repository_id/1442049765")
    error_message = "Only the platform repository may impersonate the scanner."
  }
  assert {
    condition     = toset(keys(google_artifact_registry_repository_iam_member.image_scanner)) == toset(["hopcharlie-apps"]) && alltrue([for m in google_artifact_registry_repository_iam_member.image_scanner : m.role == "roles/artifactregistry.reader"])
    error_message = "The scanner reads the app image repository and nothing else."
  }
}
run "reject_bad_platform_repository" {
  command = plan
  variables { platform_repository_id = "platform" }
  expect_failures = [var.platform_repository_id]
}
run "reject_wildcard_scan_repository" {
  command = plan
  variables { image_scan_extra_repositories = ["*"] }
  expect_failures = [var.image_scan_extra_repositories]
}
