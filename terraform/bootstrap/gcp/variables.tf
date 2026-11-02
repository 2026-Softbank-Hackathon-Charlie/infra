variable "project_id" {
  type        = string
  description = "Existing billed Google Cloud project ID."
}
variable "region" {
  type    = string
  default = "asia-northeast3"
}
variable "state_bucket_name" {
  type        = string
  description = "Globally unique GCS bucket name for Terraform state."
}
variable "github_repository_id" {
  type        = string
  description = "Immutable numeric GitHub repository ID."
  validation {
    condition     = can(regex("^[0-9]+$", var.github_repository_id))
    error_message = "Use the immutable numeric repository ID."
  }
}
variable "github_repository_owner_id" {
  type        = string
  description = "Immutable numeric GitHub organization or owner ID."
  validation {
    condition     = can(regex("^[0-9]+$", var.github_repository_owner_id))
    error_message = "Use the immutable numeric owner ID."
  }
}
variable "github_ref" {
  type    = string
  default = "refs/heads/main"
  validation {
    condition     = startswith(var.github_ref, "refs/heads/") && !strcontains(var.github_ref, "'")
    error_message = "Use one exact branch ref such as refs/heads/main."
  }
}
