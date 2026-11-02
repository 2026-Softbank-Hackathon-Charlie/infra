resource "aws_eks_pod_identity_association" "argocd" {
  for_each        = toset(["argocd-server", "argocd-application-controller", "argocd-applicationset-controller"])
  cluster_name    = module.eks.name
  namespace       = "argocd"
  service_account = each.value
  role_arn        = data.terraform_remote_state.foundation.outputs.argocd_management_role_arn
  depends_on      = [module.eks]
}
resource "aws_eks_pod_identity_association" "loki" {
  cluster_name    = module.eks.name
  namespace       = "observability"
  service_account = "loki"
  role_arn        = data.terraform_remote_state.foundation.outputs.loki_role_arn
  depends_on      = [module.eks]
}
