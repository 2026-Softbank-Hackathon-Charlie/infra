mock_provider "google" {}
variables {
  project_id                     = "charlie-test-project"
  node_service_account_email     = "charlie-gke-nodes@charlie-test-project.iam.gserviceaccount.com"
  control_plane_authorized_cidrs = ["203.0.113.10/32"]
}
run "private_nodes_restricted_api_and_storage" {
  command = plan
  assert {
    condition     = google_container_cluster.workload.private_cluster_config[0].enable_private_nodes && !google_container_cluster.workload.private_cluster_config[0].enable_private_endpoint && google_container_cluster.workload.deletion_protection && !google_container_cluster.workload.master_authorized_networks_config[0].gcp_public_cidrs_access_enabled
    error_message = "Private nodes, explicit API allowlist and deletion protection are required."
  }
  assert {
    condition     = toset([for block in google_container_cluster.workload.master_authorized_networks_config[0].cidr_blocks : block.cidr_block]) == toset(["203.0.113.10/32"])
    error_message = "The API allowlist must preserve the provided management egress CIDRs."
  }
  assert {
    condition     = google_container_cluster.workload.network_policy[0].enabled && google_container_cluster.workload.addons_config[0].gce_persistent_disk_csi_driver_config[0].enabled && google_container_cluster.workload.workload_identity_config[0].workload_pool == "charlie-test-project.svc.id.goog"
    error_message = "NetworkPolicy enforcement, PD CSI and GKE workload identity must be enabled."
  }
  assert {
    condition     = google_container_node_pool.workload.location == "asia-northeast3-a" && google_container_node_pool.workload.node_count == 1 && google_container_node_pool.workload.node_config[0].service_account == var.node_service_account_email && google_container_node_pool.workload.node_config[0].workload_metadata_config[0].mode == "GKE_METADATA" && google_container_node_pool.workload.node_config[0].shielded_instance_config[0].enable_secure_boot
    error_message = "Use one zonal node by default, dedicated identity, GKE metadata and shielded secure boot."
  }
  assert {
    condition     = !google_compute_network.workload.auto_create_subnetworks && google_compute_subnetwork.workload.private_ip_google_access && google_compute_router_nat.egress.source_subnetwork_ip_ranges_to_nat == "LIST_OF_SUBNETWORKS"
    error_message = "Use a custom VPC and scope private-node egress NAT to the workload subnet."
  }
}
run "reject_public_api_allowlist" {
  command = plan
  variables { control_plane_authorized_cidrs = ["0.0.0.0/0"] }
  expect_failures = [var.control_plane_authorized_cidrs]
}
run "reject_empty_allowlist" {
  command = plan
  variables { control_plane_authorized_cidrs = [] }
  expect_failures = [var.control_plane_authorized_cidrs]
}
run "reject_malformed_allowlist" {
  command = plan
  variables { control_plane_authorized_cidrs = ["not-a-cidr"] }
  expect_failures = [var.control_plane_authorized_cidrs]
}
run "reject_other_project_node_identity" {
  command = plan
  variables { node_service_account_email = "nodes@another-project.iam.gserviceaccount.com" }
  expect_failures = [var.node_service_account_email]
}
run "reject_zero_nodes" {
  command = plan
  variables { node_count = 0 }
  expect_failures = [var.node_count]
}
