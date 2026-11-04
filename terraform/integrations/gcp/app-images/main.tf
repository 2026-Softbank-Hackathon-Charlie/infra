locals {
  # The STS session ARN of every CodeBuild run of the build role.
  build_role   = "arn:aws:sts::${var.aws_account_id}:assumed-role/${var.aws_build_role_name}"
  registry     = "${var.region}-docker.pkg.dev"
  image_prefix = "${local.registry}/${var.project_id}/${var.repository_id}"
}

# Platform-built app images (ADR 0042). Separate from charlie-apps so an app
# build cannot overwrite the images moimroom CI publishes. GKE nodes already
# read every repository in the project (bootstrap node reader role).
resource "google_artifact_registry_repository" "apps" {
  project       = var.project_id
  location      = var.region
  repository_id = var.repository_id
  description   = "HopCharlie platform-built app images, copied from ECR for GKE (ADR 0042)"
  format        = "DOCKER"
  mode          = "STANDARD_REPOSITORY"
}

# A federation boundary of its own: AWS sessions, not Kubernetes tokens.
resource "google_iam_workload_identity_pool" "build" {
  project                   = var.project_id
  workload_identity_pool_id = var.workload_identity_pool_id
  display_name              = "Charlie app image builds"
  description               = "AWS CodeBuild app builds; not Argo CD or the platform API."
}

resource "google_iam_workload_identity_pool_provider" "build" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.build.workload_identity_pool_id
  workload_identity_pool_provider_id = var.workload_identity_provider_id
  # Google's documented AWS mapping: the role ARN without the session name.
  attribute_mapping = {
    "google.subject"     = "assertion.arn"
    "attribute.aws_role" = "assertion.arn.contains('assumed-role') ? assertion.arn.extract('{account_arn}assumed-role/') + 'assumed-role/' + assertion.arn.extract('assumed-role/{role_name}/') : assertion.arn"
  }
  attribute_condition = "attribute.aws_role == \"${local.build_role}\""
  aws {
    account_id = var.aws_account_id
  }
}

resource "google_service_account" "publisher" {
  project      = var.project_id
  account_id   = var.service_account_id
  display_name = "HopCharlie app image publisher"
  description  = "Pushes platform-built app images to ${var.repository_id} from CodeBuild (infra ADR 0042)."
}

resource "google_service_account_iam_member" "publisher" {
  service_account_id = google_service_account.publisher.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.build.name}/attribute.aws_role/${local.build_role}"
}

# Push only, on this one repository; no delete and nothing project-wide.
resource "google_artifact_registry_repository_iam_member" "publisher" {
  project    = google_artifact_registry_repository.apps.project
  location   = google_artifact_registry_repository.apps.location
  repository = google_artifact_registry_repository.apps.repository_id
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.publisher.email}"
}
