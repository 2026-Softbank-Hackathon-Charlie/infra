variable "region" {
  description = "AWS region. The hackathon account's SCP allows ap-northeast-2 and ap-southeast-2."
  type        = string
  default     = "ap-northeast-2"
}

variable "operators" {
  description = "Names that get an operator token (shown in the audit log)."
  type        = list(string)
  default     = ["seohyun", "junseok", "jongwook", "dongyun", "taehoon"]
}

variable "management_cluster" {
  description = "Management EKS cluster (infra repo) whose charlie-platform/hopcharlie-platform ServiceAccount gets the platform's AWS role."
  type        = string
  default     = "charlie-dev-management"
}

variable "onboarding_second_region" {
  description = "The other region the onboarding documents are in, besides region (the account's SCP allows ap-northeast-2 and ap-southeast-2)."
  type        = string
  default     = "ap-southeast-2"
}
