# Onboarding apps that ran on one VM with docker compose (platform ADR 0042,
# infra ADR 0082): the platform finds a GKE-side source VM by its external IP
# (instances.aggregatedList natIP) and the project's addresses. Read only:
# no SSH, serial port, metadata change or stop on GCP.
resource "google_project_iam_custom_role" "onboarding" {
  project     = var.project_id
  role_id     = "charliePlatformOnboardingDiscovery"
  title       = "HopCharlie platform onboarding discovery"
  description = "Lists the project's VM instances and addresses (infra ADR 0082)."
  permissions = ["compute.instances.list", "compute.instances.get", "compute.addresses.list"]
}

resource "google_project_iam_member" "onboarding" {
  project = var.project_id
  role    = google_project_iam_custom_role.onboarding.id
  member  = local.member
}
