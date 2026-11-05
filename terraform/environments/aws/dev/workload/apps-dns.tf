# Internal names of the shared app entry (ADR 0058): <instance>.eks.hopcharlie.internal
# resolves inside the VPC to the workload Traefik NLB. The AWS Load Balancer Controller
# creates that NLB for Service traefik/traefik, so it is found by the controller's tags
# and the record waits for it: a plan before Traefik exists, or while its NLB is being
# recreated, still succeeds and only leaves the record out. When the NLB is recreated it
# gets a new DNS name; the next apply of this root moves the record (docs/runbooks/app-entry.md).
locals {
  apps_zone = "hopcharlie.internal"
}

resource "aws_route53_zone" "apps" {
  name    = local.apps_zone
  comment = "HopCharlie app entry names inside the VPC (ADR 0058)"

  vpc {
    vpc_id = data.terraform_remote_state.foundation.outputs.vpc_id
  }

  tags = local.tags
}

data "aws_lbs" "eks_apps_entry" {
  tags = {
    "elbv2.k8s.aws/cluster" = module.eks.name
    "service.k8s.aws/stack" = "traefik/traefik"
  }
}

data "aws_lb" "eks_apps_entry" {
  count = length(data.aws_lbs.eks_apps_entry.arns) == 1 ? 1 : 0
  arn   = one(data.aws_lbs.eks_apps_entry.arns)
}

resource "aws_route53_record" "eks_apps" {
  count   = length(data.aws_lb.eks_apps_entry)
  zone_id = aws_route53_zone.apps.zone_id
  name    = "*.eks.${local.apps_zone}"
  type    = "A"

  alias {
    name                   = data.aws_lb.eks_apps_entry[0].dns_name
    zone_id                = data.aws_lb.eks_apps_entry[0].zone_id
    evaluate_target_health = false
  }
}

# More than one match (a renamed Service whose old NLB is not deleted yet, or a leaked
# NLB) also leaves the record out; warn instead of picking one.
check "eks_apps_entry_nlb" {
  assert {
    condition     = length(data.aws_lbs.eks_apps_entry.arns) == 1
    error_message = "Found ${length(data.aws_lbs.eks_apps_entry.arns)} NLBs of Service traefik/traefik, not one, so *.eks.${local.apps_zone} is not managed. See docs/runbooks/app-entry.md."
  }
}
