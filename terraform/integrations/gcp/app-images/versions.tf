terraform {
  required_version = ">= 1.10, < 2.0"
  backend "gcs" {
    prefix = "gcp/dev/app-images"
  }
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.0"
    }
  }
}
provider "google" {
  project = var.project_id
}
