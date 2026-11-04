mock_provider "aws" {
  override_during = plan
  mock_data "aws_vpc" {
    defaults = { cidr_block = "10.40.0.0/16" }
  }
  mock_resource "aws_instance" {
    defaults = { primary_network_interface_id = "eni-11111111111111111" }
  }
}
mock_provider "google" {
  override_during = plan
  mock_data "google_compute_network" {
    defaults = { self_link = "https://www.googleapis.com/compute/v1/projects/charlie-test-project/global/networks/charlie-dev-gcp-workload-vpc" }
  }
  mock_data "google_compute_subnetwork" {
    defaults = {
      self_link     = "https://www.googleapis.com/compute/v1/projects/charlie-test-project/regions/asia-northeast3/subnetworks/charlie-dev-gcp-workload-subnet"
      network       = "https://www.googleapis.com/compute/v1/projects/charlie-test-project/global/networks/charlie-dev-gcp-workload-vpc"
      ip_cidr_range = "10.80.0.0/20"
    }
  }
  mock_resource "google_compute_instance" {
    defaults = { self_link = "https://www.googleapis.com/compute/v1/projects/charlie-test-project/zones/asia-northeast3-a/instances/charlie-gcp-app-router" }
  }
}
override_data {
  target = data.aws_subnet.management["subnet-11111111111111111"]
  values = { vpc_id = "vpc-11111111111111111", cidr_block = "10.40.0.0/20" }
}
override_data {
  target = data.aws_subnet.management["subnet-22222222222222222"]
  values = { vpc_id = "vpc-11111111111111111", cidr_block = "10.40.16.0/20" }
}
override_data {
  target = data.aws_route_table.management["subnet-11111111111111111"]
  values = { id = "rtb-11111111111111111", vpc_id = "vpc-11111111111111111" }
}
override_data {
  target = data.aws_route_table.management["subnet-22222222222222222"]
  values = { id = "rtb-22222222222222222", vpc_id = "vpc-11111111111111111" }
}
variables {
  aws_account_id            = "123456789012"
  aws_vpc_id                = "vpc-11111111111111111"
  aws_management_subnet_ids = ["subnet-11111111111111111", "subnet-22222222222222222"]
  aws_router_ami_id         = "ami-11111111111111111"
  project_id                = "charlie-test-project"
  gcp_router_image          = "projects/debian-cloud/global/images/debian-12-test-image"
}

run "only_management_to_primary_app_routes" {
  command = plan
  assert {
    condition     = output.aws_gateway_cidr == "10.40.0.0/19" && output.gcp_app_cidr == "10.80.0.0/20" && length(aws_route.gcp_apps) == 2 && alltrue([for route in aws_route.gcp_apps : route.destination_cidr_block == "10.80.0.0/20" && route.network_interface_id == "eni-11111111111111111"]) && google_compute_route.aws_management.dest_range == "10.40.0.0/19" && google_compute_route.aws_management.next_hop_instance == google_compute_instance.router.self_link
    error_message = "Only the management subnets and GKE primary app subnet may use the dedicated routers; no default, pod or database routes."
  }
  assert {
    condition     = jsondecode(output.tailscale_policy_candidate).grants == [{ src = ["10.40.0.0/19"], dst = ["10.80.0.0/20"], ip = ["tcp:80"] }] && jsondecode(output.tailscale_policy_candidate).acls == [] && jsondecode(output.tailscale_policy_candidate).ssh == []
    error_message = "The tailnet policy may open only app HTTP from the management subnet, with no legacy all-access or SSH grant."
  }
}

run "routers_and_management_are_private_and_bounded" {
  command = plan
  assert {
    condition     = !aws_instance.router.associate_public_ip_address && !aws_instance.router.source_dest_check && aws_instance.router.root_block_device[0].encrypted && aws_instance.router.metadata_options[0].http_tokens == "required" && google_compute_instance.router.can_ip_forward && length(google_compute_instance.router.network_interface[0].access_config) == 0 && length(google_compute_instance.router.service_account) == 0
    error_message = "Dedicated forwarding VMs must have private NICs, encrypted storage/IMDS safeguards and no GCP cloud identity."
  }
  assert {
    condition     = length(aws_security_group.router.ingress) == 1 && one(aws_security_group.router.ingress).protocol == "tcp" && one(aws_security_group.router.ingress).from_port == 80 && one(aws_security_group.router.ingress).to_port == 80 && toset(one(aws_security_group.router.ingress).cidr_blocks) == toset(["10.40.0.0/19"]) && alltrue([for rule in aws_security_group.router.egress : (rule.protocol == "tcp" && rule.from_port == 443 && rule.to_port == 443 && toset(rule.cidr_blocks) == toset(["0.0.0.0/0"])) || (contains(["tcp", "udp"], rule.protocol) && rule.from_port == 53 && rule.to_port == 53 && toset(rule.cidr_blocks) == toset(["10.40.0.2/32"]))])
    error_message = "The AWS router accepts only management app HTTP; only DERP/control HTTPS and the VPC DNS resolver may leave."
  }
  assert {
    condition     = toset(google_compute_firewall.router_iap.source_ranges) == toset(["35.235.240.0/20"]) && toset(one(google_compute_firewall.router_iap.allow).ports) == toset(["5252"]) && google_compute_firewall.router_iap.priority < google_compute_firewall.router_ingress_deny.priority && one(google_compute_firewall.router_ingress_deny.deny).protocol == "all" && toset(google_compute_firewall.router_ingress_deny.source_ranges) == toset(["0.0.0.0/0"]) && toset(google_compute_firewall.router_ingress_deny.target_tags) == toset(["charlie-app-router"])
    error_message = "Only IAP may reach the GCP vendor web UI; all other router ingress, including SSH, must be denied before broader internal rules."
  }
  assert {
    condition     = toset(google_compute_firewall.router_apps.destination_ranges) == toset(["10.80.0.0/20"]) && toset(one(google_compute_firewall.router_apps.allow).ports) == toset(["80"]) && google_compute_firewall.router_egress_deny.priority > google_compute_firewall.router_apps.priority && one(google_compute_firewall.router_egress_deny.deny).protocol == "all" && google_project_service.iap.service == "iap.googleapis.com" && !google_project_service.iap.disable_on_destroy
    error_message = "GCP forwards only application HTTP and preserves the separately owned IAP API during teardown."
  }
  assert {
    condition     = length(jsondecode(aws_iam_role_policy.session_manager.policy).Statement) == 1 && toset(jsondecode(aws_iam_role_policy.session_manager.policy).Statement[0].Action) == toset(["ssm:UpdateInstanceInformation", "ssmmessages:CreateControlChannel", "ssmmessages:CreateDataChannel", "ssmmessages:OpenControlChannel", "ssmmessages:OpenDataChannel"]) && jsondecode(aws_iam_role_policy.session_manager.policy).Statement[0].Condition.StringEquals["aws:RequestedRegion"] == "ap-northeast-2"
    error_message = "The router identity may only maintain Session Manager channels and heartbeat, never read parameters/secrets or send commands."
  }
}

run "reject_workload_subnet_as_gateway" {
  command = plan
  override_data {
    target = data.aws_subnet.management["subnet-11111111111111111"]
    values = { vpc_id = "vpc-11111111111111111", cidr_block = "10.40.32.0/20" }
  }
  expect_failures = [aws_instance.router]
}

run "reject_pod_network_as_app_destination" {
  command = plan
  override_data {
    target = data.google_compute_subnetwork.workload
    values = {
      self_link     = "https://www.googleapis.com/compute/v1/projects/charlie-test-project/regions/asia-northeast3/subnetworks/charlie-dev-gcp-workload-subnet"
      network       = "https://www.googleapis.com/compute/v1/projects/charlie-test-project/global/networks/charlie-dev-gcp-workload-vpc"
      ip_cidr_range = "10.81.0.0/16"
    }
  }
  expect_failures = [google_compute_instance.router]
}
