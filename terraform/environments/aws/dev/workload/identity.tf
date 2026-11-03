# The CNPG operator creates these Cluster-named ServiceAccounts in charlie-db.
# Both the active and temporary restore Cluster need their own credentials.
resource "aws_eks_pod_identity_association" "cnpg" {
  for_each = {
    source = {
      service_account = "postgres-poc"
      role_arn        = data.terraform_remote_state.foundation.outputs.cnpg_source_role_arn
    }
    restore = {
      service_account = "postgres-poc-restore"
      role_arn        = data.terraform_remote_state.foundation.outputs.cnpg_restore_role_arn
    }
  }

  cluster_name         = module.eks.name
  namespace            = "charlie-db"
  service_account      = each.value.service_account
  role_arn             = each.value.role_arn
  disable_session_tags = false
  depends_on           = [module.eks]
}
