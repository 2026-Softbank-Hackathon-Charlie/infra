terraform {
  required_version = ">= 1.10, < 2.0"
  backend "gcs" {
    prefix = "gcp/dev/rin"
  }
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "= 7.46.2"
    }
    aws = {
      source  = "hashicorp/aws"
      version = "= 6.67.0"
    }
  }
}
provider "google" {
  project = var.project_id
  region  = "asia-northeast3"
  zone    = "asia-northeast3-a"
}
provider "aws" {
  region              = "ap-northeast-2"
  allowed_account_ids = [var.aws_account_id]
}
