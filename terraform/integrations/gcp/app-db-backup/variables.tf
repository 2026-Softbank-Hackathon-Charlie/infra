variable "project_id" {
  type        = string
  description = "Existing Google Cloud project that runs the GKE workload cluster."
}
variable "region" {
  type        = string
  default     = "asia-northeast3"
  description = "Bucket location; the GKE cluster's region, so backups stay in the country."
}
variable "bucket_name" {
  type        = string
  description = "Globally unique bucket for the backups of the app databases on GKE."
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$", var.bucket_name))
    error_message = "Use a 3-63 character bucket name with lowercase letters, digits and internal hyphens."
  }
}
variable "service_account_id" {
  type    = string
  default = "charlie-app-db-backup"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.service_account_id))
    error_message = "Use a valid 6-30 character Google service account ID."
  }
}
variable "clusters" {
  type        = list(string)
  default     = ["charlie-apps/moimroom-db-gke"]
  description = "CNPG Clusters (namespace/name) whose Kubernetes service account, named after the Cluster, may act as the backup account."
  validation {
    condition     = length(var.clusters) > 0 && alltrue([for c in var.clusters : can(regex("^[a-z0-9]([a-z0-9-]*[a-z0-9])?/[a-z0-9]([a-z0-9-]*[a-z0-9])?$", c))])
    error_message = "Name each Cluster exactly as namespace/name; wildcards are not allowed."
  }
}
