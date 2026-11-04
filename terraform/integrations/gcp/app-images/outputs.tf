output "registry" {
  description = "Host the build logs in to (docker login)."
  value       = local.registry
}
output "image_prefix" {
  description = "GKE images are <image_prefix>/<app>/<service>@<digest>."
  value       = local.image_prefix
}
output "publisher_service_account_email" {
  value = google_service_account.publisher.email
}
output "workload_identity_provider" {
  value = google_iam_workload_identity_pool_provider.build.name
}
output "federation_config" {
  description = "Public ADC configuration for the CodeBuild build (AWS credentials of the build role); no key or token."
  value = {
    type                              = "external_account"
    audience                          = "//iam.googleapis.com/${google_iam_workload_identity_pool_provider.build.name}"
    subject_token_type                = "urn:ietf:params:aws:token-type:aws4_request"
    token_url                         = "https://sts.googleapis.com/v1/token"
    service_account_impersonation_url = "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/${google_service_account.publisher.email}:generateAccessToken"
    credential_source = {
      environment_id                 = "aws1"
      regional_cred_verification_url = "https://sts.{region}.amazonaws.com?Action=GetCallerIdentity&Version=2011-06-15"
    }
  }
}
