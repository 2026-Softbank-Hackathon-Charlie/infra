variable "enable_langfuse_storage" {
  description = "Keep the deployed management Langfuse Pod Identity association enabled."
  type        = bool
  default     = true
}

resource "aws_eks_pod_identity_association" "langfuse" {
  count           = var.enable_langfuse_storage ? 1 : 0
  cluster_name    = module.eks.name
  namespace       = "langfuse"
  service_account = "langfuse"
  role_arn        = try(data.terraform_remote_state.foundation.outputs.langfuse_storage.role_arn, null)
  lifecycle {
    precondition {
      condition     = try(data.terraform_remote_state.foundation.outputs.langfuse_storage.role_arn != null, false)
      error_message = "Enable and prepare foundation Langfuse storage before the association."
    }
  }
}
