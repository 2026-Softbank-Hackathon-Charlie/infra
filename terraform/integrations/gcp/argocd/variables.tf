variable "project_id" {
  type        = string
  description = "Existing Google Cloud project; APIs and runtime are managed separately."
}
variable "eks_oidc_issuer_url" {
  type        = string
  description = "Exact management EKS cluster OIDC issuer, not the Kubernetes API endpoint."
  validation {
    condition     = can(regex("^https://oidc\\.eks\\.[a-z0-9-]+\\.amazonaws\\.com/id/[A-Fa-f0-9]{32}$", var.eks_oidc_issuer_url))
    error_message = "Use the exact HTTPS EKS OIDC issuer ending in its 32-character cluster ID."
  }
}
variable "argocd_namespace" {
  type    = string
  default = "argocd"
  validation {
    condition     = length(var.argocd_namespace) <= 63 && can(regex("^[a-z0-9]([-a-z0-9]*[a-z0-9])?$", var.argocd_namespace))
    error_message = "Use one exact Kubernetes namespace; wildcards are not allowed."
  }
}
variable "workload_identity_pool_id" {
  type    = string
  default = "charlie-argocd"
  validation {
    condition     = length(var.workload_identity_pool_id) >= 4 && length(var.workload_identity_pool_id) <= 32 && can(regex("^[a-z][a-z0-9-]*[a-z0-9]$", var.workload_identity_pool_id)) && !startswith(var.workload_identity_pool_id, "gcp-")
    error_message = "Use a distinct 4-32 character pool ID, without the reserved gcp- prefix."
  }
}
variable "workload_identity_provider_id" {
  type    = string
  default = "management-eks"
  validation {
    condition     = length(var.workload_identity_provider_id) >= 4 && length(var.workload_identity_provider_id) <= 32 && can(regex("^[a-z][a-z0-9-]*[a-z0-9]$", var.workload_identity_provider_id)) && !startswith(var.workload_identity_provider_id, "gcp-")
    error_message = "Use a 4-32 character provider ID, without the reserved gcp- prefix."
  }
}
variable "service_account_id" {
  type    = string
  default = "charlie-argocd-gke"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.service_account_id))
    error_message = "Use a valid 6-30 character Google service account ID."
  }
}
