variable "aws_account_id" {
  description = "배포를 허용할 AWS 계정 ID. 예시를 실제 계정으로 교체합니다."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id)) && var.aws_account_id != "000000000000"
    error_message = "실제 12자리 AWS 계정 ID가 필요합니다."
  }
}
variable "aws_region" {
  description = "AWS 리전"
  type        = string
  default     = "ap-northeast-2"
}
variable "project" {
  description = "자원 이름·태그에 사용하는 프로젝트 이름"
  type        = string
  default     = "charlie"
}
variable "environment" {
  description = "환경 이름"
  type        = string
  default     = "dev"
}
variable "github_oidc_subject_prefix" {
  description = "GitHub API actions/oidc/customization/sub의 sub_claim_prefix. main 브랜치만 신뢰합니다."
  type        = string

  validation {
    condition     = can(regex("^repo:[A-Za-z0-9_.-]+(@[0-9]+)?/[A-Za-z0-9_.-]+(@[0-9]+)?$", var.github_oidc_subject_prefix))
    error_message = "repo:OWNER/REPO 또는 repo:OWNER@ID/REPO@ID 형식의 subject prefix가 필요합니다."
  }
}
variable "management_cluster_name" {
  description = "Must match foundation's management cluster name."
  type        = string
  default     = "charlie-dev-management"
}
variable "workload_cluster_name" {
  description = "Must match foundation's workload cluster name."
  type        = string
  default     = "charlie-dev-workload"
}

variable "github_oidc_provider_arn" {
  description = "기존 GitHub OIDC provider ARN. 빈 문자열일 때만 생성하며 기존 state 소유권은 이전하지 않습니다."
  type        = string
  default     = ""
  validation {
    condition     = var.github_oidc_provider_arn == "" || var.github_oidc_provider_arn == "arn:aws:iam::${var.aws_account_id}:oidc-provider/token.actions.githubusercontent.com"
    error_message = "같은 AWS 계정의 GitHub OIDC provider ARN을 지정하세요."
  }
}
