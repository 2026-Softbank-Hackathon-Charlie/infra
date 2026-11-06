# Security view (platform SEC-03, ADR 0066): the platform API reads Security
# Command Center findings of this project with the identity it already has.
# Read only. SCC Standard (free) is activated once in the console; Terraform
# has no activation resource.

# Only the API; bootstrap/gcp's API list does not include it. Never disabled
# on destroy, so removing this file cannot switch SCC off.
resource "google_project_service" "securitycenter" {
  project                    = var.project_id
  service                    = "securitycenter.googleapis.com"
  disable_on_destroy         = false
  disable_dependent_services = false
}

# Findings and sources, read only; no mute, no state change, no settings.
resource "google_project_iam_member" "scc_findings" {
  project = var.project_id
  role    = "roles/securitycenter.findingsViewer"
  member  = local.member
}
