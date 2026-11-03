terraform {
  required_version = ">= 1.10"
  required_providers {
    aws        = { source = "hashicorp/aws", version = "~> 6.71" }
    cloudflare = { source = "cloudflare/cloudflare", version = "5.28.0" }
  }
  backend "s3" {
    bucket       = "hopcharlie-tfstate-525091405886-apne2"
    key          = "platform/headlamp/terraform.tfstate"
    region       = "ap-northeast-2"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region              = "ap-northeast-2"
  allowed_account_ids = ["525091405886"]
  default_tags {
    tags = { Project = "charlie", Component = "headlamp", ManagedBy = "Terraform" }
  }
}
provider "cloudflare" {} # CLOUDFLARE_API_TOKEN; never put a token in tfvars or Git.

variable "host" {
  type    = string
  default = "headlamp.hopcharlie.dev"
}
variable "cloudflare_zone_id" {
  type    = string
  default = "257a362c84334d1b91c48d10b88fc266"
}
variable "cluster_names" {
  type    = set(string)
  default = ["charlie-dev-management", "charlie-dev-workload"]
}

data "aws_eks_cluster" "target" {
  for_each = var.cluster_names
  name     = each.value
}
data "aws_lb" "management" {
  name = "charlie-management-external"
}

resource "aws_cognito_user_pool" "headlamp" {
  name                = "charlie-headlamp-team"
  user_pool_tier      = "LITE"
  deletion_protection = "ACTIVE"
  admin_create_user_config {
    allow_admin_create_user_only = true
  }
  password_policy {
    minimum_length                   = 14
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 7
  }
  account_recovery_setting {
    recovery_mechanism {
      name     = "admin_only"
      priority = 1
    }
  }
}

resource "aws_cognito_user_pool_domain" "headlamp" {
  domain       = "charlie-headlamp-525091405886"
  user_pool_id = aws_cognito_user_pool.headlamp.id
}

resource "aws_cognito_user_pool_client" "headlamp" {
  name                                 = "headlamp-web"
  user_pool_id                         = aws_cognito_user_pool.headlamp.id
  generate_secret                      = true
  supported_identity_providers         = ["COGNITO"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "profile", "email"]
  callback_urls                        = ["https://${var.host}/oidc-callback"]
  logout_urls                          = ["https://${var.host}/"]
  prevent_user_existence_errors        = "ENABLED"
  access_token_validity                = 1
  id_token_validity                    = 1
  refresh_token_validity               = 1
  token_validity_units {
    access_token  = "hours"
    id_token      = "hours"
    refresh_token = "days"
  }
}

resource "aws_cognito_user_group" "viewers" {
  name         = "team-viewers"
  user_pool_id = aws_cognito_user_pool.headlamp.id
  description  = "Read-only Headlamp access; membership is assigned by an administrator."
}

resource "aws_eks_identity_provider_config" "headlamp" {
  for_each     = var.cluster_names
  cluster_name = each.value
  oidc {
    identity_provider_config_name = "headlamp-team"
    issuer_url                    = "https://${aws_cognito_user_pool.headlamp.endpoint}"
    client_id                     = aws_cognito_user_pool_client.headlamp.id
    username_claim                = "sub"
    username_prefix               = "headlamp:"
    groups_claim                  = "cognito:groups"
    groups_prefix                 = "headlamp:"
    required_claims               = { token_use = "id" }
  }
}

# The management nodes carry their cluster SG. Permit only their HTTPS calls
# to the other cluster APIs; keep all Kubernetes API endpoints private.
resource "aws_vpc_security_group_ingress_rule" "headlamp_api" {
  for_each                     = setsubtract(var.cluster_names, toset(["charlie-dev-management"]))
  security_group_id            = data.aws_eks_cluster.target[each.value].vpc_config[0].cluster_security_group_id
  referenced_security_group_id = data.aws_eks_cluster.target["charlie-dev-management"].vpc_config[0].cluster_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  description                  = "Headlamp management nodes to private workload API"
}

resource "cloudflare_dns_record" "headlamp" {
  zone_id = var.cloudflare_zone_id
  name    = var.host
  type    = "CNAME"
  content = data.aws_lb.management.dns_name
  ttl     = 300
  proxied = false
  comment = "Team Headlamp on the shared management ALB"
}

output "public_config" {
  value = {
    host = var.host
    oidc = {
      issuerURL = "https://${aws_cognito_user_pool.headlamp.endpoint}"
      clientID  = aws_cognito_user_pool_client.headlamp.id
    }
    clusters = [for name in sort(tolist(var.cluster_names)) : {
      name     = name
      endpoint = data.aws_eks_cluster.target[name].endpoint
      ca       = data.aws_eks_cluster.target[name].certificate_authority[0].data
    }]
  }
}
output "client_secret" {
  value     = aws_cognito_user_pool_client.headlamp.client_secret
  sensitive = true
}
output "user_pool_id" {
  value = aws_cognito_user_pool.headlamp.id
}
