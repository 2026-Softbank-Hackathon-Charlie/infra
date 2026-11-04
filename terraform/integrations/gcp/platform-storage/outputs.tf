output "platform_service_account_email" {
  value = google_service_account.platform.email
}
output "workload_identity_provider" {
  value = google_iam_workload_identity_pool_provider.platform.name
}
output "kubernetes_service_account_subject" {
  value = local.subject
}
output "projected_token_audience" {
  description = "Audience of the projected token the platform API pod mounts (gcp.tokenAudience)."
  value       = local.token_audience
}
output "federation_config" {
  description = "Public ADC configuration for the platform chart (gcp.credentialConfig); no key or token."
  value = {
    type                              = "external_account"
    audience                          = "//iam.googleapis.com/${local.provider_name}"
    subject_token_type                = "urn:ietf:params:oauth:token-type:jwt"
    token_url                         = "https://sts.googleapis.com/v1/token"
    service_account_impersonation_url = "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/${google_service_account.platform.email}:generateAccessToken"
    credential_source                 = { file = "/var/run/secrets/gcp/token" }
  }
}
