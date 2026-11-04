locals {
  aws_router_name  = "charlie-aws-app-router"
  gcp_router_name  = "charlie-gcp-app-router"
  gcp_router_tag   = "charlie-app-router"
  aws_gateway_cidr = cidrsubnet(var.aws_vpc_cidr, 3, 0)
  gcp_app_cidr     = data.google_compute_subnetwork.workload.ip_cidr_range
}

# Read selected network metadata, never another stack's state or secret values.
data "aws_vpc" "shared" { id = var.aws_vpc_id }
data "aws_subnet" "management" {
  for_each = var.aws_management_subnet_ids
  id       = each.value
}
data "aws_route_table" "management" {
  for_each = var.aws_management_subnet_ids
  filter {
    name   = "association.subnet-id"
    values = [each.value]
  }
}
data "google_compute_network" "workload" {
  project = var.project_id
  name    = "${var.gcp_cluster_name}-vpc"
}
data "google_compute_subnetwork" "workload" {
  project = var.project_id
  region  = var.region
  name    = "${var.gcp_cluster_name}-subnet"
}

resource "aws_security_group" "router" {
  name        = local.aws_router_name
  description = "Management app HTTP transit; admin UI is loopback over Session Manager"
  vpc_id      = var.aws_vpc_id
  ingress {
    protocol    = "tcp"
    from_port   = 80
    to_port     = 80
    cidr_blocks = [local.aws_gateway_cidr]
  }
  egress {
    protocol    = "tcp"
    from_port   = 443
    to_port     = 443
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    protocol    = "udp"
    from_port   = 53
    to_port     = 53
    cidr_blocks = ["${cidrhost(var.aws_vpc_cidr, 2)}/32"]
  }
  egress {
    protocol    = "tcp"
    from_port   = 53
    to_port     = 53
    cidr_blocks = ["${cidrhost(var.aws_vpc_cidr, 2)}/32"]
  }
}

resource "aws_iam_role" "router" {
  name = local.aws_router_name
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "ec2.amazonaws.com" } }]
  })
}
resource "aws_iam_role_policy" "session_manager" {
  name = "session-manager-only"
  role = aws_iam_role.router.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = ["ssm:UpdateInstanceInformation", "ssmmessages:CreateControlChannel", "ssmmessages:CreateDataChannel", "ssmmessages:OpenControlChannel", "ssmmessages:OpenDataChannel"]
      Resource  = "*"
      Condition = { StringEquals = { "aws:RequestedRegion" = var.aws_region } }
    }]
  })
}
resource "aws_iam_instance_profile" "router" {
  name = local.aws_router_name
  role = aws_iam_role.router.name
}
resource "aws_instance" "router" {
  ami                         = var.aws_router_ami_id
  instance_type               = "t3.micro"
  subnet_id                   = sort(tolist(var.aws_management_subnet_ids))[0]
  associate_public_ip_address = false
  source_dest_check           = false
  vpc_security_group_ids      = [aws_security_group.router.id]
  iam_instance_profile        = aws_iam_instance_profile.router.name
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }
  root_block_device {
    volume_type = "gp3"
    volume_size = 8
    encrypted   = true
  }
  credit_specification { cpu_credits = "standard" }
  user_data = templatefile("${path.module}/router-bootstrap.sh.tftpl", {
    hostname       = local.aws_router_name
    advertise_cidr = local.aws_gateway_cidr
    advertise_tag  = "tag:charlie-aws-router"
    web_listen     = "127.0.0.1:5252"
    remote_cidr    = local.gcp_app_cidr
  })
  tags       = { Name = local.aws_router_name }
  depends_on = [aws_iam_role_policy.session_manager]
  lifecycle {
    precondition {
      condition = (
        data.aws_vpc.shared.cidr_block == var.aws_vpc_cidr &&
        alltrue([for subnet in data.aws_subnet.management : subnet.vpc_id == var.aws_vpc_id]) &&
        toset([for subnet in data.aws_subnet.management : subnet.cidr_block]) == toset([cidrsubnet(var.aws_vpc_cidr, 4, 0), cidrsubnet(var.aws_vpc_cidr, 4, 1)]) &&
        alltrue([for table in data.aws_route_table.management : table.vpc_id == var.aws_vpc_id]) &&
        length(toset([for table in data.aws_route_table.management : table.id])) == 2
      )
      error_message = "Only the existing two management private subnets and their distinct route tables may use this router."
    }
  }
}
resource "aws_route" "gcp_apps" {
  for_each               = var.aws_management_subnet_ids
  route_table_id         = data.aws_route_table.management[each.value].id
  destination_cidr_block = local.gcp_app_cidr
  network_interface_id   = aws_instance.router.primary_network_interface_id
}

resource "google_project_service" "iap" {
  project            = var.project_id
  service            = "iap.googleapis.com"
  disable_on_destroy = false
}
resource "google_compute_instance" "router" {
  project        = var.project_id
  name           = local.gcp_router_name
  zone           = var.zone
  machine_type   = "e2-micro"
  can_ip_forward = true
  tags           = [local.gcp_router_tag]
  boot_disk {
    initialize_params {
      image = var.gcp_router_image
      type  = "pd-balanced"
      size  = 10
    }
  }
  network_interface { subnetwork = data.google_compute_subnetwork.workload.self_link }
  # No access_config, SSH key or service account is attached to the router.
  metadata = { block-project-ssh-keys = "true", disable-legacy-endpoints = "true" }
  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }
  metadata_startup_script = templatefile("${path.module}/router-bootstrap.sh.tftpl", {
    hostname       = local.gcp_router_name
    advertise_cidr = local.gcp_app_cidr
    advertise_tag  = "tag:charlie-gcp-router"
    web_listen     = "0.0.0.0:5252"
    remote_cidr    = local.aws_gateway_cidr
  })
  depends_on = [google_project_service.iap]
  lifecycle {
    precondition {
      condition     = data.google_compute_subnetwork.workload.network == data.google_compute_network.workload.self_link && local.gcp_app_cidr == "10.80.0.0/20"
      error_message = "Use only the existing GKE primary subnet; pod, database and default routes are not advertised."
    }
  }
}
resource "google_compute_route" "aws_management" {
  project           = var.project_id
  name              = "charlie-apps-to-aws-management"
  network           = data.google_compute_network.workload.self_link
  dest_range        = local.aws_gateway_cidr
  next_hop_instance = google_compute_instance.router.self_link
  priority          = 1000
}

resource "google_compute_firewall" "router_https" {
  project            = var.project_id
  name               = "charlie-app-router-https"
  network            = data.google_compute_network.workload.self_link
  direction          = "EGRESS"
  priority           = 1000
  target_tags        = [local.gcp_router_tag]
  destination_ranges = ["0.0.0.0/0"]
  allow {
    protocol = "tcp"
    ports    = ["443"]
  }
}
resource "google_compute_firewall" "router_apps" {
  project            = var.project_id
  name               = "charlie-app-router-app-http"
  network            = data.google_compute_network.workload.self_link
  direction          = "EGRESS"
  priority           = 1000
  target_tags        = [local.gcp_router_tag]
  destination_ranges = [local.gcp_app_cidr]
  allow {
    protocol = "tcp"
    ports    = ["80"]
  }
}
resource "google_compute_firewall" "router_dns" {
  project            = var.project_id
  name               = "charlie-app-router-dns"
  network            = data.google_compute_network.workload.self_link
  direction          = "EGRESS"
  priority           = 1000
  target_tags        = [local.gcp_router_tag]
  destination_ranges = ["169.254.169.254/32"]
  allow {
    protocol = "udp"
    ports    = ["53"]
  }
  allow {
    protocol = "tcp"
    ports    = ["53"]
  }
}
resource "google_compute_firewall" "router_egress_deny" {
  project            = var.project_id
  name               = "charlie-app-router-egress-deny"
  network            = data.google_compute_network.workload.self_link
  direction          = "EGRESS"
  priority           = 2000
  target_tags        = [local.gcp_router_tag]
  destination_ranges = ["0.0.0.0/0"]
  deny { protocol = "all" }
}
resource "google_compute_firewall" "router_iap" {
  project       = var.project_id
  name          = "charlie-app-router-iap-web"
  network       = data.google_compute_network.workload.self_link
  direction     = "INGRESS"
  priority      = 900
  target_tags   = [local.gcp_router_tag]
  source_ranges = ["35.235.240.0/20"]
  allow {
    protocol = "tcp"
    ports    = ["5252"]
  }
}
resource "google_compute_firewall" "router_ingress_deny" {
  project       = var.project_id
  name          = "charlie-app-router-ingress-deny"
  network       = data.google_compute_network.workload.self_link
  direction     = "INGRESS"
  priority      = 910
  target_tags   = [local.gcp_router_tag]
  source_ranges = ["0.0.0.0/0"]
  deny { protocol = "all" }
}
