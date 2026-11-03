mock_provider "google" {
  override_during = plan
  mock_data "google_project" { defaults = { number = "123456789012" } }
  mock_resource "google_iam_workload_identity_pool" { defaults = { name = "projects/123456789012/locations/global/workloadIdentityPools/charlie-argocd" } }
  mock_resource "google_iam_workload_identity_pool_provider" { defaults = { name = "projects/123456789012/locations/global/workloadIdentityPools/charlie-argocd/providers/management-eks" } }
  mock_resource "google_service_account" {
    defaults = {
      name  = "projects/charlie-test-project/serviceAccounts/charlie-argocd-gke@charlie-test-project.iam.gserviceaccount.com"
      email = "charlie-argocd-gke@charlie-test-project.iam.gserviceaccount.com"
    }
  }
}
variables {
  project_id          = "charlie-test-project"
  eks_oidc_issuer_url = "https://oidc.eks.ap-northeast-2.amazonaws.com/id/11111111111111111111111111111111"
}
run "only_management_argocd_service_accounts_are_trusted" {
  command = plan
  assert {
    condition     = google_iam_workload_identity_pool_provider.argocd.oidc[0].issuer_uri == var.eks_oidc_issuer_url && google_iam_workload_identity_pool_provider.argocd.attribute_mapping == tomap({ "google.subject" = "assertion.sub" })
    error_message = "Use the management EKS issuer and preserve the Kubernetes subject."
  }
  assert {
    condition     = google_iam_workload_identity_pool_provider.argocd.attribute_condition == "assertion.sub in [\"system:serviceaccount:argocd:argocd-application-controller\",\"system:serviceaccount:argocd:argocd-server\"]"
    error_message = "Trust exactly the two Argo service accounts, not a namespace or pool."
  }
  assert {
    condition     = toset(google_iam_workload_identity_pool_provider.argocd.oidc[0].allowed_audiences) == toset([output.projected_token_audience]) && output.projected_token_audience == "https://iam.googleapis.com/projects/123456789012/locations/global/workloadIdentityPools/charlie-argocd/providers/management-eks"
    error_message = "Projected token and provider must share the exact HTTPS audience."
  }
}
run "google_permissions_are_discovery_and_exact_subject_impersonation_only" {
  command = plan
  assert {
    condition     = google_project_iam_member.gke_discovery.role == "roles/container.clusterViewer" && google_project_iam_member.gke_discovery.member == "serviceAccount:${output.deploy_service_account_email}"
    error_message = "Google IAM must be discovery-only; Kubernetes RBAC controls deployment."
  }
  assert {
    condition     = toset(keys(google_service_account_iam_member.argocd)) == toset(output.kubernetes_service_account_subjects) && alltrue([for subject, binding in google_service_account_iam_member.argocd : binding.role == "roles/iam.workloadIdentityUser" && binding.member == "principal://iam.googleapis.com/projects/123456789012/locations/global/workloadIdentityPools/charlie-argocd/subject/${subject}"])
    error_message = "Allow impersonation only for each exact Kubernetes subject."
  }
}
run "public_adc_uses_rotating_projected_jwt_and_impersonation" {
  command = plan
  assert {
    condition     = output.federation_config.type == "external_account" && output.federation_config.subject_token_type == "urn:ietf:params:oauth:token-type:jwt" && output.federation_config.token_url == "https://sts.googleapis.com/v1/token" && output.federation_config.credential_source.file == "/var/run/secrets/gcp/token"
    error_message = "Use file-based projected JWT federation, not a stored key."
  }
  assert {
    condition     = output.federation_config.audience == "//iam.googleapis.com/projects/123456789012/locations/global/workloadIdentityPools/charlie-argocd/providers/management-eks" && output.federation_config.service_account_impersonation_url == "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/${output.deploy_service_account_email}:generateAccessToken"
    error_message = "ADC must target this provider and only its dedicated Google service account."
  }
}
run "reject_insecure_issuer" {
  command = plan
  variables { eks_oidc_issuer_url = "http://oidc.eks.ap-northeast-2.amazonaws.com/id/11111111111111111111111111111111" }
  expect_failures = [var.eks_oidc_issuer_url]
}
run "reject_non_eks_issuer" {
  command = plan
  variables { eks_oidc_issuer_url = "https://token.actions.githubusercontent.com" }
  expect_failures = [var.eks_oidc_issuer_url]
}
run "reject_wildcard_namespace" {
  command = plan
  variables { argocd_namespace = "*" }
  expect_failures = [var.argocd_namespace]
}
