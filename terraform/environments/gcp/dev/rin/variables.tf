variable "project_id" {
  type    = string
  default = "project-b47cdba3-db5b-42a2-9a6"
  validation {
    condition     = var.project_id == "project-b47cdba3-db5b-42a2-9a6"
    error_message = "This root manages only the approved rin project."
  }
}
variable "github_repository_id" {
  type    = string
  default = "1443173546"
  validation {
    condition     = var.github_repository_id == "1443173546"
    error_message = "Trust only the immutable rin repository ID."
  }
}
variable "github_repository_owner_id" {
  type    = string
  default = "350278309"
  validation {
    condition     = var.github_repository_owner_id == "350278309"
    error_message = "Trust only the immutable Charlie organization ID."
  }
}
variable "aws_account_id" {
  type    = string
  default = "525091405886"
  validation {
    condition     = var.aws_account_id == "525091405886"
    error_message = "Use only the existing rin AWS account."
  }
}
variable "ssm_kms_key_arn" {
  type        = string
  description = "Public key ARN from describe-key alias/aws/ssm; never a parameter value."
  default     = "arn:aws:kms:ap-northeast-2:525091405886:key/b33d433b-2bcc-4923-9daa-361c57730c6d"
  validation {
    condition     = can(regex("^arn:aws:kms:ap-northeast-2:525091405886:key/[0-9a-f-]{36}$", var.ssm_kms_key_arn))
    error_message = "Use the rin SSM KMS key ARN in the approved account and region."
  }
}
