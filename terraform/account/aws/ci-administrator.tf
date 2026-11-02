# The repository main-branch OIDC trust remains in main.tf. This attachment
# grants account-wide administration; legacy policies are not permission limits.
resource "aws_iam_role_policy_attachment" "administrator" {
  role       = aws_iam_role.terraform_apply.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}
