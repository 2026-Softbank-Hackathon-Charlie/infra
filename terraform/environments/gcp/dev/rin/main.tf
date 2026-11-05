data "google_compute_network" "default" {
  project = var.project_id
  name    = "default"
}
locals {
  zone               = "asia-northeast3-a"
  instance_name      = "hopcharlie-rin"
  network_tag        = "hopcharlie-rin"
  deploy_member      = "serviceAccount:${google_service_account.deploy.email}"
  parameter_path_arn = "arn:aws:ssm:ap-northeast-2:${var.aws_account_id}:parameter/rin"
  parameter_arn      = "${local.parameter_path_arn}/*"
}
resource "google_compute_instance" "rin" {
  project             = var.project_id
  name                = local.instance_name
  zone                = local.zone
  machine_type        = "e2-standard-2"
  tags                = [local.network_tag]
  deletion_protection = true
  labels              = { app = "rin", environment = "dev", managed-by = "terraform" }
  boot_disk {
    auto_delete = false
    initialize_params {
      image = "projects/ubuntu-os-cloud/global/images/family/ubuntu-2404-lts-amd64"
      size  = 40
      type  = "pd-balanced"
    }
  }
  network_interface {
    network    = data.google_compute_network.default.self_link
    stack_type = "IPV4_ONLY"
    # Outbound apt/ECR/Slack access; ingress is denied except IAP SSH.
    access_config {}
  }
  # Deliberately no service_account block: no metadata credential to steal.
  metadata = {
    enable-oslogin           = "TRUE"
    block-project-ssh-keys   = "TRUE"
    disable-legacy-endpoints = "TRUE"
    startup-script           = file("${path.module}/startup.sh")
  }
  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }
  lifecycle { prevent_destroy = true }
  depends_on = [google_compute_firewall.iap_ssh, google_compute_firewall.ingress_deny]
}
resource "google_compute_firewall" "iap_ssh" {
  project       = var.project_id
  name          = "hopcharlie-rin-iap-ssh"
  network       = data.google_compute_network.default.self_link
  direction     = "INGRESS"
  priority      = 800
  source_ranges = ["35.235.240.0/20"]
  target_tags   = [local.network_tag]
  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}
resource "google_compute_firewall" "ingress_deny" {
  project       = var.project_id
  name          = "hopcharlie-rin-deny-ingress"
  network       = data.google_compute_network.default.self_link
  direction     = "INGRESS"
  priority      = 900
  source_ranges = ["0.0.0.0/0"]
  target_tags   = [local.network_tag]
  deny { protocol = "all" }
}
# Dedicated pool prevents a different provider in the infra/runner pool from
# satisfying this service account's principalSet binding.
resource "google_iam_workload_identity_pool" "rin" {
  project                   = var.project_id
  workload_identity_pool_id = "charlie-rin-deploy"
  display_name              = "Rin GitHub deploy only"
}
resource "google_iam_workload_identity_pool_provider" "rin" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.rin.workload_identity_pool_id
  workload_identity_pool_provider_id = "rin-main"
  attribute_mapping = {
    "google.subject"                = "assertion.sub"
    "attribute.repository_id"       = "assertion.repository_id"
    "attribute.repository_owner_id" = "assertion.repository_owner_id"
  }
  attribute_condition = "assertion.repository_id == '${var.github_repository_id}' && assertion.repository_owner_id == '${var.github_repository_owner_id}' && assertion.ref == 'refs/heads/main'"
  oidc { issuer_uri = "https://token.actions.githubusercontent.com" }
}
resource "google_service_account" "deploy" {
  project      = var.project_id
  account_id   = "charlie-rin-deploy"
  display_name = "Rin CI IAP deployer; never attached to VM"
}
resource "google_service_account_iam_member" "github" {
  service_account_id = google_service_account.deploy.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.rin.name}/attribute.repository_id/${var.github_repository_id}"
}
resource "google_compute_instance_iam_member" "os_admin" {
  project       = var.project_id
  zone          = local.zone
  instance_name = google_compute_instance.rin.name
  role          = "roles/compute.osAdminLogin"
  member        = local.deploy_member
}
resource "google_iap_tunnel_instance_iam_member" "ssh" {
  project  = var.project_id
  zone     = local.zone
  instance = google_compute_instance.rin.name
  role     = "roles/iap.tunnelResourceAccessor"
  member   = local.deploy_member
  condition {
    title      = "ssh-only"
    expression = "destination.port == 22"
  }
}
# gcloud SSH/SCP needs project discovery/list; instances.get and login are
# supplied only on rin by the instance-level OS Admin Login binding above.
resource "google_project_iam_custom_role" "discovery" {
  project     = var.project_id
  role_id     = "rinDeployDiscovery"
  title       = "Rin deploy project discovery"
  permissions = ["compute.projects.get", "compute.instances.list"]
}
resource "google_project_iam_member" "discovery" {
  project = var.project_id
  role    = google_project_iam_custom_role.discovery.id
  member  = local.deploy_member
}
# Own exactly this new inline policy; the existing role/trust, push policy,
# EC2 and rin/deploy/aws state remain under their existing owner.
resource "aws_iam_role_policy" "ssm_read" {
  name = "rin-gcp-deploy-read"
  role = "rin-deploy"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadRinEnvironment"
        Effect   = "Allow"
        Action   = ["ssm:GetParametersByPath"]
        Resource = [local.parameter_path_arn, local.parameter_arn]
      },
      {
        Sid      = "DecryptRinParametersViaSSM"
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = var.ssm_kms_key_arn
        Condition = {
          StringEquals = { "kms:ViaService" = "ssm.ap-northeast-2.amazonaws.com" }
          StringLike   = { "kms:EncryptionContext:PARAMETER_ARN" = local.parameter_arn }
        }
      }
    ]
  })
}
