terraform {
  required_version = ">= 1.10, < 2.0"
  # Own key in the shared AWS state bucket; nothing else reads this state.
  backend "s3" {
    bucket       = "charlie-tfstate-525091405886-ap-northeast-2"
    key          = "fixtures/aws/legacy-moimroom/terraform.tfstate"
    region       = "ap-northeast-2"
    encrypt      = true
    use_lockfile = true
  }
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "= 6.71.0"
    }
  }
}

provider "aws" {
  region              = "ap-northeast-2"
  allowed_account_ids = [var.aws_account_id]
  default_tags {
    tags = local.tags
  }
}
