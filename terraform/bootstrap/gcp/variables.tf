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
variable "enable_moimroom_image_publishing" {
  type        = bool
  default     = false
  nullable    = false
  description = "Create the dedicated main-branch moimroom image publisher."
}
variable "moimroom_repository_id" {
  type        = string
  default     = ""
  nullable    = false
  description = "Immutable numeric moimroom repository ID; required when image publishing is enabled."
  validation {
    condition     = var.moimroom_repository_id == "" || can(regex("^[0-9]+$", var.moimroom_repository_id))
    error_message = "Use the immutable numeric moimroom repository ID, or omit it when publishing is disabled."
  }
  validation {
    condition     = !var.enable_moimroom_image_publishing || var.moimroom_repository_id != ""
    error_message = "moimroom_repository_id is required when enable_moimroom_image_publishing is true."
  }
}

variable "enable_cnpg_poc_backup" {
  type        = bool
  default     = false
  nullable    = false
  description = "Create a dedicated native GCS backup bucket and workload identity for charlie-db/postgres-poc."
}

variable "cnpg_poc_backup_bucket_name" {
  type        = string
  default     = ""
  nullable    = false
  description = "Globally unique CNPG backup bucket name; required when backup is enabled."
  validation {
    condition     = var.cnpg_poc_backup_bucket_name == "" || can(regex("^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$", var.cnpg_poc_backup_bucket_name))
    error_message = "Use a 3-63 character bucket name with lowercase letters, digits and internal hyphens."
  }
  validation {
    condition     = !var.enable_cnpg_poc_backup || var.cnpg_poc_backup_bucket_name != ""
    error_message = "cnpg_poc_backup_bucket_name is required when enable_cnpg_poc_backup is true."
  }
}
