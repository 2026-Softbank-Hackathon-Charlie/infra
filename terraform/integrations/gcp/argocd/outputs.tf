output "deploy_service_account_email" {
  value = google_service_account.argocd.email
}
output "workload_identity_pool" {
  value = google_iam_workload_identity_pool.argocd.name
}
output "workload_identity_provider" {
  value = google_iam_workload_identity_pool_provider.argocd.name
}
output "kubernetes_service_account_subjects" {
  value = sort(tolist(local.subjects))
}
output "projected_token_audience" {
  value = local.token_audience
}
output "federation_config" {
  description = "Public ADC configuration; contains no private key or issued token."
  value = {
    type                              = "external_account"
    audience                          = "//iam.googleapis.com/${local.provider_name}"
    subject_token_type                = "urn:ietf:params:oauth:token-type:jwt"
    token_url                         = "https://sts.googleapis.com/v1/token"
    service_account_impersonation_url = "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/${google_service_account.argocd.email}:generateAccessToken"
    credential_source                 = { file = "/var/run/secrets/gcp/token" }
  }
}
