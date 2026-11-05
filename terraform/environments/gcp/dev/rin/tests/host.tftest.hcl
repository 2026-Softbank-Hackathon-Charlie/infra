mock_provider "google" {
  override_during = plan
  mock_data "google_compute_network" { defaults = { self_link = "https://www.googleapis.com/compute/v1/projects/project-b47cdba3-db5b-42a2-9a6/global/networks/default" } }
  mock_resource "google_service_account" {
    defaults = {
      name  = "projects/project-b47cdba3-db5b-42a2-9a6/serviceAccounts/charlie-rin-deploy@project-b47cdba3-db5b-42a2-9a6.iam.gserviceaccount.com"
      email = "charlie-rin-deploy@project-b47cdba3-db5b-42a2-9a6.iam.gserviceaccount.com"
    }
  }
  mock_resource "google_iam_workload_identity_pool" { defaults = { name = "projects/414068132732/locations/global/workloadIdentityPools/charlie-rin-deploy" } }
  mock_resource "google_iam_workload_identity_pool_provider" { defaults = { name = "projects/414068132732/locations/global/workloadIdentityPools/charlie-rin-deploy/providers/rin-main" } }
  mock_resource "google_project_iam_custom_role" { defaults = { id = "projects/project-b47cdba3-db5b-42a2-9a6/roles/rinDeployDiscovery" } }
}
mock_provider "aws" { override_during = plan }
run "dedicated_equal_size_host_has_no_runtime_credentials" {
  command = plan
  assert {
    condition     = google_compute_instance.rin.name == "hopcharlie-rin" && google_compute_instance.rin.zone == "asia-northeast3-a" && google_compute_instance.rin.machine_type == "e2-standard-2" && google_compute_instance.rin.boot_disk[0].initialize_params[0].size == 40 && google_compute_instance.rin.boot_disk[0].initialize_params[0].type == "pd-balanced"
    error_message = "Rin must remain a dedicated 2 vCPU/8 GiB/40 GiB Seoul host."
  }
  assert {
    condition     = google_compute_instance.rin.boot_disk[0].initialize_params[0].image == "projects/ubuntu-os-cloud/global/images/family/ubuntu-2404-lts-amd64" && length(google_compute_instance.rin.service_account) == 0 && google_compute_instance.rin.deletion_protection && !google_compute_instance.rin.boot_disk[0].auto_delete
    error_message = "Use Ubuntu 24 x86 without a VM SA; protect retained application data."
  }
  assert {
    condition     = google_compute_instance.rin.metadata["enable-oslogin"] == "TRUE" && google_compute_instance.rin.metadata["block-project-ssh-keys"] == "TRUE" && google_compute_instance.rin.metadata["disable-legacy-endpoints"] == "TRUE" && google_compute_instance.rin.shielded_instance_config[0].enable_secure_boot && google_compute_instance.rin.network_interface[0].network == data.google_compute_network.default.self_link
    error_message = "Use OS Login and shielded boot in the existing default network."
  }
  assert {
    condition     = google_compute_instance.rin.network_interface[0].stack_type == "IPV4_ONLY" && length(google_compute_instance.rin.network_interface[0].access_config) == 1 && google_compute_instance.rin.metadata["startup-script"] == file("${path.module}/startup.sh")
    error_message = "Outbound internet is required for apt/ECR/Slack; bootstrap must be the reviewed local file."
  }
}
run "iap_only_ssh_overrides_default_network_ingress" {
  command = plan
  assert {
    condition     = google_compute_firewall.iap_ssh.priority == 800 && google_compute_firewall.ingress_deny.priority == 900 && toset(google_compute_firewall.iap_ssh.source_ranges) == toset(["35.235.240.0/20"]) && one(google_compute_firewall.iap_ssh.allow).protocol == "tcp" && toset(one(google_compute_firewall.iap_ssh.allow).ports) == toset(["22"])
    error_message = "Only IAP tcp/22 may precede the deny."
  }
  assert {
    condition     = one(google_compute_firewall.ingress_deny.deny).protocol == "all" && toset(google_compute_firewall.ingress_deny.source_ranges) == toset(["0.0.0.0/0"]) && toset(google_compute_firewall.ingress_deny.target_tags) == toset(["hopcharlie-rin"]) && toset(google_compute_firewall.iap_ssh.target_tags) == toset(google_compute_instance.rin.tags) && google_compute_firewall.iap_ssh.direction == "INGRESS" && google_compute_firewall.ingress_deny.direction == "INGRESS"
    error_message = "Rin-tag-only deny must override permissive default internal/SSH rules."
  }
}
run "immutable_rin_main_trust_and_vm_scoped_admin" {
  command = plan
  assert {
    condition     = google_iam_workload_identity_pool.rin.workload_identity_pool_id == "charlie-rin-deploy" && google_iam_workload_identity_pool_provider.rin.attribute_condition == "assertion.repository_id == '1443173546' && assertion.repository_owner_id == '350278309' && assertion.ref == 'refs/heads/main'" && google_iam_workload_identity_pool_provider.rin.oidc[0].issuer_uri == "https://token.actions.githubusercontent.com"
    error_message = "Use a dedicated pool/provider trusting only immutable rin owner/repo IDs and main."
  }
  assert {
    condition     = google_service_account_iam_member.github.member == "principalSet://iam.googleapis.com/projects/414068132732/locations/global/workloadIdentityPools/charlie-rin-deploy/attribute.repository_id/1443173546" && google_service_account_iam_member.github.role == "roles/iam.workloadIdentityUser" && google_service_account.deploy.account_id == "charlie-rin-deploy"
    error_message = "Never reuse infra or runner identities for rin."
  }
  assert {
    condition     = google_compute_instance_iam_member.os_admin.instance_name == "hopcharlie-rin" && google_compute_instance_iam_member.os_admin.role == "roles/compute.osAdminLogin" && google_compute_instance_iam_member.os_admin.member == "serviceAccount:${output.deploy_service_account}" && google_iap_tunnel_instance_iam_member.ssh.instance == "hopcharlie-rin" && google_iap_tunnel_instance_iam_member.ssh.role == "roles/iap.tunnelResourceAccessor" && google_iap_tunnel_instance_iam_member.ssh.condition[0].expression == "destination.port == 22"
    error_message = "Sudo OS Login and IAP are limited to rin; only SSH can be tunneled."
  }
  assert {
    condition     = toset(google_project_iam_custom_role.discovery.permissions) == toset(["compute.projects.get", "compute.instances.list"]) && google_project_iam_member.discovery.role == google_project_iam_custom_role.discovery.id && google_project_iam_member.discovery.member == "serviceAccount:${output.deploy_service_account}"
    error_message = "Project-level permissions are discovery only, never compute/IAM mutation or other-VM login."
  }
}
run "aws_read_policy_owns_only_new_name_and_rin_parameters" {
  command = plan
  assert {
    condition     = aws_iam_role_policy.ssm_read.name == "rin-gcp-deploy-read" && aws_iam_role_policy.ssm_read.role == "rin-deploy" && length(jsondecode(aws_iam_role_policy.ssm_read.policy).Statement) == 2 && jsondecode(aws_iam_role_policy.ssm_read.policy).Statement[0].Action == ["ssm:GetParametersByPath"] && toset(jsondecode(aws_iam_role_policy.ssm_read.policy).Statement[0].Resource) == toset(["arn:aws:ssm:ap-northeast-2:525091405886:parameter/rin", "arn:aws:ssm:ap-northeast-2:525091405886:parameter/rin/*"])
    error_message = "Own only a new inline policy; allow only the rin path and its descendants for GetParametersByPath."
  }
  assert {
    condition     = jsondecode(aws_iam_role_policy.ssm_read.policy).Statement[1].Action == ["kms:Decrypt"] && jsondecode(aws_iam_role_policy.ssm_read.policy).Statement[1].Resource == var.ssm_kms_key_arn && jsondecode(aws_iam_role_policy.ssm_read.policy).Statement[1].Condition.StringEquals["kms:ViaService"] == "ssm.ap-northeast-2.amazonaws.com" && jsondecode(aws_iam_role_policy.ssm_read.policy).Statement[1].Condition.StringLike["kms:EncryptionContext:PARAMETER_ARN"] == "arn:aws:ssm:ap-northeast-2:525091405886:parameter/rin/*"
    error_message = "Decrypt only the explicit key for rin parameters through SSM."
  }
}
run "public_outputs_match_rin_ci" {
  command = plan
  assert {
    condition     = output.project_id == "project-b47cdba3-db5b-42a2-9a6" && output.zone == "asia-northeast3-a" && output.instance_name == "hopcharlie-rin" && output.workload_identity_provider == "projects/414068132732/locations/global/workloadIdentityPools/charlie-rin-deploy/providers/rin-main" && output.deploy_service_account == "charlie-rin-deploy@project-b47cdba3-db5b-42a2-9a6.iam.gserviceaccount.com"
    error_message = "Expose only the public identifiers consumed by rin's GCP variables."
  }
}
run "reject_other_repository" {
  command = plan
  variables { github_repository_id = "1" }
  expect_failures = [var.github_repository_id]
}
run "reject_other_owner" {
  command = plan
  variables { github_repository_owner_id = "1" }
  expect_failures = [var.github_repository_owner_id]
}
run "reject_other_aws_key" {
  command = plan
  variables { ssm_kms_key_arn = "arn:aws:kms:us-east-1:111111111111:key/b33d433b-2bcc-4923-9daa-361c57730c6d" }
  expect_failures = [var.ssm_kms_key_arn]
}
