variable "aws_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id)) && var.aws_account_id != "000000000000"
    error_message = "Use the existing 12-digit AWS account ID."
  }
}
variable "aws_region" {
  type    = string
  default = "ap-northeast-2"
  validation {
    condition     = var.aws_region == "ap-northeast-2"
    error_message = "This application path is scoped to the existing Seoul AWS region."
  }
}
variable "aws_vpc_id" {
  type = string
  validation {
    condition     = can(regex("^vpc-[0-9a-f]+$", var.aws_vpc_id))
    error_message = "Use the existing shared AWS VPC ID."
  }
}
variable "aws_vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
  validation {
    condition     = startswith(var.aws_vpc_cidr, "10.") && can(cidrnetmask(var.aws_vpc_cidr)) && try("${cidrhost(var.aws_vpc_cidr, 0)}/16" == var.aws_vpc_cidr, false)
    error_message = "Use the existing canonical private IPv4 /16 VPC, whose first two /20 subnets belong to management."
  }
}
variable "aws_management_subnet_ids" {
  type = set(string)
  validation {
    condition     = length(var.aws_management_subnet_ids) == 2 && alltrue([for id in var.aws_management_subnet_ids : can(regex("^subnet-[0-9a-f]+$", id))])
    error_message = "Use the existing two management private subnet IDs, not the workload or public subnets."
  }
}
variable "aws_router_ami_id" {
  type        = string
  description = "Reviewed pinned Amazon Linux 2023 x86_64 AMI with the preinstalled SSM agent."
  validation {
    condition     = can(regex("^ami-([0-9a-f]{8}|[0-9a-f]{17})$", var.aws_router_ami_id))
    error_message = "Use one pinned official Amazon Linux AMI ID, not a moving SSM parameter."
  }
}
variable "project_id" {
  type        = string
  description = "Existing GCP workload project; no new project or workload cluster is created."
}
variable "region" {
  type    = string
  default = "asia-northeast3"
  validation {
    condition     = var.region == "asia-northeast3"
    error_message = "Use the existing Seoul GCP region."
  }
}
variable "zone" {
  type    = string
  default = "asia-northeast3-a"
  validation {
    condition     = var.zone == "asia-northeast3-a"
    error_message = "Use the existing workload zone."
  }
}
variable "gcp_cluster_name" {
  type    = string
  default = "charlie-dev-gcp-workload"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,48}[a-z0-9]$", var.gcp_cluster_name))
    error_message = "Use the existing workload cluster name; network and subnet names are derived from it."
  }
}
variable "gcp_router_image" {
  type        = string
  description = "Reviewed pinned x86_64 Debian/Ubuntu image resource name, without a moving image family."
  validation {
    condition     = can(regex("^projects/(debian-cloud|ubuntu-os-cloud)/global/images/[a-z][a-z0-9-]+$", var.gcp_router_image))
    error_message = "Use a pinned official Debian/Ubuntu image resource name."
  }
}
