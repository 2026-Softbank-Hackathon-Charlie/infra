variable "project_id" {
  type = string
}
variable "region" {
  type    = string
  default = "asia-northeast3"
}
variable "zone" {
  type    = string
  default = "asia-northeast3-a"
}
variable "cluster_name" {
  type    = string
  default = "charlie-dev-gcp-workload"
}
variable "node_service_account_email" {
  type        = string
  description = "Dedicated node identity created by bootstrap/gcp."
  validation {
    condition     = endswith(var.node_service_account_email, "@${var.project_id}.iam.gserviceaccount.com")
    error_message = "Use the dedicated node service account from this project's bootstrap outputs."
  }
}
variable "control_plane_authorized_cidrs" {
  type        = list(string)
  description = "Explicit IPv4 egress CIDRs for management/operators. No all-addresses default."
  validation {
    condition     = length(var.control_plane_authorized_cidrs) > 0 && alltrue([for cidr in var.control_plane_authorized_cidrs : can(cidrnetmask(cidr)) && try(tonumber(split("/", cidr)[1]) > 0, false)])
    error_message = "Supply at least one IPv4 CIDR; /0 is not permitted."
  }
}
variable "subnet_cidr" {
  type    = string
  default = "10.80.0.0/20"
}
variable "pod_cidr" {
  type    = string
  default = "10.81.0.0/16"
}
variable "service_cidr" {
  type    = string
  default = "10.82.0.0/20"
}
variable "master_cidr" {
  type    = string
  default = "172.30.0.0/28"
}
variable "machine_type" {
  type    = string
  default = "e2-standard-2"
}
variable "node_count" {
  type    = number
  default = 3
  validation {
    condition     = var.node_count >= 1 && var.node_count <= 3 && floor(var.node_count) == var.node_count
    error_message = "This demo supports one to three nodes in one zone."
  }
}
variable "deletion_protection" {
  type    = bool
  default = true
}
