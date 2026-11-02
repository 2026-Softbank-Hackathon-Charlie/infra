resource "google_compute_network" "workload" {
  name                    = "${var.cluster_name}-vpc"
  auto_create_subnetworks = false
}
resource "google_compute_subnetwork" "workload" {
  name                     = "${var.cluster_name}-subnet"
  region                   = var.region
  network                  = google_compute_network.workload.id
  ip_cidr_range            = var.subnet_cidr
  private_ip_google_access = true
  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = var.pod_cidr
  }
  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = var.service_cidr
  }
}
resource "google_compute_router" "egress" {
  name    = "${var.cluster_name}-router"
  region  = var.region
  network = google_compute_network.workload.id
}
resource "google_compute_router_nat" "egress" {
  name                               = "${var.cluster_name}-nat"
  router                             = google_compute_router.egress.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"
  subnetwork {
    name                    = google_compute_subnetwork.workload.id
    source_ip_ranges_to_nat = ["ALL_IP_RANGES"]
  }
}
resource "google_container_cluster" "workload" {
  name                     = var.cluster_name
  location                 = var.zone
  deletion_protection      = var.deletion_protection
  network                  = google_compute_network.workload.id
  subnetwork               = google_compute_subnetwork.workload.id
  remove_default_node_pool = true
  initial_node_count       = 1
  networking_mode          = "VPC_NATIVE"
  enable_shielded_nodes    = true
  release_channel { channel = "REGULAR" }
  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }
  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false
    master_ipv4_cidr_block  = var.master_cidr
  }
  master_authorized_networks_config {
    gcp_public_cidrs_access_enabled = false
    dynamic "cidr_blocks" {
      for_each = toset(var.control_plane_authorized_cidrs)
      content { cidr_block = cidr_blocks.value }
    }
  }
  workload_identity_config { workload_pool = "${var.project_id}.svc.id.goog" }
  network_policy {
    enabled  = true
    provider = "CALICO"
  }
  addons_config {
    network_policy_config { disabled = false }
    gce_persistent_disk_csi_driver_config { enabled = true }
  }
  node_config {
    service_account = var.node_service_account_email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
  }
}
resource "google_container_node_pool" "workload" {
  name       = "workload"
  location   = var.zone
  cluster    = google_container_cluster.workload.name
  node_count = var.node_count
  management {
    auto_repair  = true
    auto_upgrade = true
  }
  node_config {
    machine_type    = var.machine_type
    disk_size_gb    = 30
    disk_type       = "pd-balanced"
    service_account = var.node_service_account_email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    metadata        = { disable-legacy-endpoints = "true" }
    workload_metadata_config { mode = "GKE_METADATA" }
    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }
  }
  depends_on = [google_compute_router_nat.egress]
}
