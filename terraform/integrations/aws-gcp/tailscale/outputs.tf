output "aws_router_instance_id" { value = aws_instance.router.id }
output "aws_router_private_ip" { value = aws_instance.router.private_ip }
output "gcp_router_name" { value = google_compute_instance.router.name }
output "gcp_router_zone" { value = google_compute_instance.router.zone }
output "gcp_router_private_ip" { value = google_compute_instance.router.network_interface[0].network_ip }
output "aws_gateway_cidr" { value = local.aws_gateway_cidr }
output "gcp_app_cidr" { value = local.gcp_app_cidr }
output "tailscale_policy_candidate" {
  description = "Non-secret policy for separate owner review; Terraform does not mutate the tailnet."
  value = templatefile("${path.module}/policy.json.tftpl", {
    aws_gateway_cidr = local.aws_gateway_cidr
    gcp_app_cidr     = local.gcp_app_cidr
  })
}
