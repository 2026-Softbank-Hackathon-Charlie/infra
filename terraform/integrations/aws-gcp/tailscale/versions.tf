terraform {
  required_version = ">= 1.10, < 2.0"
  backend "gcs" {}
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "= 6.71.0"
    }
    google = {
      source  = "hashicorp/google"
      version = "= 7.46.2"
    }
  }
}

provider "aws" {
  region              = var.aws_region
  allowed_account_ids = [var.aws_account_id]
  default_tags {
    tags = { Project = "charlie", Environment = "dev", Component = "app-private-network", ManagedBy = "Terraform" }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}
