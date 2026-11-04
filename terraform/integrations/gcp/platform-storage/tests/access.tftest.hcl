mock_provider "google" {
  override_during = plan
  mock_data "google_project" { defaults = { number = "123456789012" } }
  mock_resource "google_iam_workload_identity_pool" { defaults = { name = "projects/123456789012/locations/global/workloadIdentityPools/charlie-platform" } }
  mock_resource "google_iam_workload_identity_pool_provider" { defaults = { name = "projects/123456789012/locations/global/workloadIdentityPools/charlie-platform/providers/management-eks" } }
  mock_resource "google_service_account" {
    defaults = {
      name  = "projects/charlie-test-project/serviceAccounts/charlie-platform-storage@charlie-test-project.iam.gserviceaccount.com"
      email = "charlie-platform-storage@charlie-test-project.iam.gserviceaccount.com"
    }
  }
  mock_resource "google_project_iam_custom_role" { defaults = { id = "projects/charlie-test-project/roles/mock" } }
}
variables {
  project_id          = "charlie-test-project"
  eks_oidc_issuer_url = "https://oidc.eks.ap-northeast-2.amazonaws.com/id/11111111111111111111111111111111"
}
run "only_the_platform_service_account_is_trusted" {
  command = plan
  assert {
    condition     = google_iam_workload_identity_pool_provider.platform.oidc[0].issuer_uri == var.eks_oidc_issuer_url && google_iam_workload_identity_pool_provider.platform.attribute_mapping == tomap({ "google.subject" = "assertion.sub" })
    error_message = "Use the management EKS issuer and preserve the Kubernetes subject."
  }
  assert {
    condition     = google_iam_workload_identity_pool_provider.platform.attribute_condition == "assertion.sub == \"system:serviceaccount:charlie-platform:hopcharlie-platform\""
    error_message = "Trust exactly the platform service account."
  }
  assert {
    condition     = toset(google_iam_workload_identity_pool_provider.platform.oidc[0].allowed_audiences) == toset([output.projected_token_audience]) && output.projected_token_audience == "https://iam.googleapis.com/projects/123456789012/locations/global/workloadIdentityPools/charlie-platform/providers/management-eks"
    error_message = "Projected token and provider must share the exact HTTPS audience."
  }
  assert {
    condition     = google_iam_workload_identity_pool.platform.workload_identity_pool_id != "charlie-argocd"
    error_message = "Keep the platform pool separate from Argo CD's (ADR 0021)."
  }
  assert {
    condition     = google_service_account_iam_member.platform.role == "roles/iam.workloadIdentityUser" && google_service_account_iam_member.platform.member == "principal://iam.googleapis.com/projects/123456789012/locations/global/workloadIdentityPools/charlie-platform/subject/system:serviceaccount:charlie-platform:hopcharlie-platform"
    error_message = "Allow impersonation only for the exact subject."
  }
}
run "bucket_permissions_are_limited_to_app_buckets" {
  command = plan
  assert {
    condition     = google_project_iam_member.buckets.condition[0].expression == "resource.name.startsWith(\"projects/_/buckets/hopcharlie-gke-\")"
    error_message = "Bucket and object permissions apply only to hopcharlie-gke-* buckets."
  }
  assert {
    condition     = length([for p in google_project_iam_custom_role.buckets.permissions : p if !startswith(p, "storage.buckets.") && !startswith(p, "storage.objects.")]) == 0 && !contains(google_project_iam_custom_role.buckets.permissions, "storage.objects.get") && !contains(google_project_iam_custom_role.buckets.permissions, "storage.objects.create")
    error_message = "The platform empties and deletes app buckets; it does not read or write app files."
  }
  assert {
    condition     = length(google_project_iam_member.identities.condition) == 0 && length([for p in google_project_iam_custom_role.identities.permissions : p if !startswith(p, "iam.serviceAccounts.") && !startswith(p, "storage.hmacKeys.")]) == 0
    error_message = "Project-wide permissions are only service accounts and HMAC keys."
  }
  assert {
    condition     = length(setintersection(toset(google_project_iam_custom_role.identities.permissions), toset(["iam.serviceAccounts.actAs", "iam.serviceAccounts.getAccessToken", "iam.serviceAccountKeys.create", "iam.serviceAccounts.setIamPolicy", "resourcemanager.projects.setIamPolicy"]))) == 0
    error_message = "No impersonation, service account keys or project IAM changes."
  }
}
run "public_adc_uses_rotating_projected_jwt_and_impersonation" {
  command = plan
  assert {
    condition     = output.federation_config.type == "external_account" && output.federation_config.credential_source.file == "/var/run/secrets/gcp/token" && output.federation_config.audience == "//iam.googleapis.com/projects/123456789012/locations/global/workloadIdentityPools/charlie-platform/providers/management-eks"
    error_message = "Use file-based projected JWT federation for this provider, not a stored key."
  }
  assert {
    condition     = output.federation_config.service_account_impersonation_url == "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/${output.platform_service_account_email}:generateAccessToken"
    error_message = "ADC must impersonate only the platform storage service account."
  }
}
run "reject_non_eks_issuer" {
  command = plan
  variables { eks_oidc_issuer_url = "https://token.actions.githubusercontent.com" }
  expect_failures = [var.eks_oidc_issuer_url]
}
run "reject_wildcard_namespace" {
  command = plan
  variables { platform_namespace = "*" }
  expect_failures = [var.platform_namespace]
}
run "reject_long_bucket_prefix" {
  command = plan
  variables { bucket_prefix = "hopcharlie-gke-apps-" }
  expect_failures = [var.bucket_prefix]
}
