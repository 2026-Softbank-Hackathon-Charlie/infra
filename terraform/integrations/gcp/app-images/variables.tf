variable "project_id" {
  type        = string
  description = "Existing Google Cloud project that runs the GKE workload cluster."
}
variable "region" {
  type        = string
  default     = "asia-northeast3"
  description = "Artifact Registry location; the GKE cluster's region."
}
variable "aws_account_id" {
  type        = string
  description = "AWS account of the hopcharlie-app-build CodeBuild project (infra ADR 0022)."
  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "Use the 12-digit AWS account ID."
  }
}
variable "aws_build_role_name" {
  type        = string
  default     = "hopcharlie-app-build"
  description = "IAM role the CodeBuild project runs as; only its sessions are trusted."
  validation {
    condition     = can(regex("^[A-Za-z0-9+=,.@_-]{1,64}$", var.aws_build_role_name))
    error_message = "Use one exact IAM role name; wildcards are not allowed."
  }
}
variable "repository_id" {
  type        = string
  default     = "hopcharlie-apps"
  description = "Artifact Registry repository for platform-built app images."
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,61}[a-z0-9]$", var.repository_id))
    error_message = "Use a valid repository ID."
  }
}
variable "workload_identity_pool_id" {
  type    = string
  default = "charlie-app-build"
  validation {
    condition     = length(var.workload_identity_pool_id) >= 4 && length(var.workload_identity_pool_id) <= 32 && can(regex("^[a-z][a-z0-9-]*[a-z0-9]$", var.workload_identity_pool_id)) && !startswith(var.workload_identity_pool_id, "gcp-") && !contains(["charlie-argocd", "charlie-platform"], var.workload_identity_pool_id)
    error_message = "Use a distinct 4-32 character pool ID, not Argo CD's or the platform API's."
  }
}
variable "workload_identity_provider_id" {
  type    = string
  default = "aws-codebuild"
  validation {
    condition     = length(var.workload_identity_provider_id) >= 4 && length(var.workload_identity_provider_id) <= 32 && can(regex("^[a-z][a-z0-9-]*[a-z0-9]$", var.workload_identity_provider_id)) && !startswith(var.workload_identity_provider_id, "gcp-")
    error_message = "Use a 4-32 character provider ID, without the reserved gcp- prefix."
  }
}
variable "service_account_id" {
  type    = string
  default = "charlie-app-image-publisher"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.service_account_id))
    error_message = "Use a valid 6-30 character Google service account ID."
  }
}
variable "github_owner_id" {
  type        = string
  description = "Immutable numeric ID of the GitHub organization that owns the platform repository."
  validation {
    condition     = can(regex("^[0-9]+$", var.github_owner_id))
    error_message = "Use the immutable numeric owner ID."
  }
}
variable "platform_repository_id" {
  type        = string
  description = "Immutable numeric ID of the platform repository whose scan-images workflow reads app images."
  validation {
    condition     = can(regex("^[0-9]+$", var.platform_repository_id))
    error_message = "Use the immutable numeric repository ID."
  }
}
variable "image_scan_extra_repositories" {
  type        = list(string)
  default     = []
  description = "Other Artifact Registry repositories in this project and region the image scanner may read."
  validation {
    condition     = alltrue([for r in var.image_scan_extra_repositories : can(regex("^[a-z][a-z0-9-]{0,61}[a-z0-9]$", r))])
    error_message = "Use exact repository IDs; wildcards are not allowed."
  }
}
