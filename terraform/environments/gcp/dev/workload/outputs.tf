output "cluster_name" { value = google_container_cluster.workload.name }
output "cluster_location" { value = google_container_cluster.workload.location }
output "cluster_endpoint" {
  value     = google_container_cluster.workload.endpoint
  sensitive = true
}
output "network_name" { value = google_compute_network.workload.name }
output "subnet_name" { value = google_compute_subnetwork.workload.name }
