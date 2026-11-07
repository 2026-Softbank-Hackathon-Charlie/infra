variable "aws_account_id" {
  description = "The hackathon AWS account; the provider refuses any other."
  type        = string
  default     = "525091405886"
}

variable "availability_zone" {
  description = "AZ of the default-VPC subnet the VM lands in."
  type        = string
  default     = "ap-northeast-2a"
}

variable "domain" {
  description = "Public hostname of the app. Only used in outputs; the DNS record is created by hand."
  type        = string
  default     = "moimroom-legacy.hopcharlie.dev"
}

variable "deploy_ssh_public_key" {
  description = "OpenSSH public key for the deploy user (the matching private key is the repo secret DEPLOY_SSH_KEY)."
  type        = string
  validation {
    condition     = can(regex("^(ssh-ed25519|ecdsa-sha2-nistp256|ssh-rsa) [A-Za-z0-9+/=]+( .*)?$", trimspace(var.deploy_ssh_public_key)))
    error_message = "Use one OpenSSH public key line (ssh-ed25519 recommended)."
  }
}

variable "ssh_allowed_cidrs" {
  description = <<-EOT
    IPv4 CIDRs allowed to reach port 22: the egress IPs of the self-hosted GitHub
    runners that run the deploy workflow (label hopcharlie), as /32. See README.md
    for how to look them up.
  EOT
  type        = list(string)
  validation {
    condition     = length(var.ssh_allowed_cidrs) > 0 && alltrue([for c in var.ssh_allowed_cidrs : can(cidrnetmask(c)) && c != "0.0.0.0/0"])
    error_message = "List at least one IPv4 CIDR, and never 0.0.0.0/0."
  }
}
