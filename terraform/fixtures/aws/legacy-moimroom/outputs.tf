output "instance_id" { value = aws_instance.vm.id }
output "public_ip" { value = aws_eip.vm.public_ip }
output "s3_bucket" { value = aws_s3_bucket.photos.id }

output "dns_record" {
  description = "Create this by hand in Cloudflare."
  value       = "A ${var.domain} -> ${aws_eip.vm.public_ip} (DNS only, not proxied, TTL Auto)"
}

output "deploy_secrets" {
  description = "Non-secret GitHub repo secrets of the fixture repo."
  value       = { DEPLOY_HOST = aws_eip.vm.public_ip, DEPLOY_USER = "deploy" }
}

output "s3_access_key_id" {
  value     = aws_iam_access_key.app.id
  sensitive = true
}

output "s3_secret_access_key" {
  value     = aws_iam_access_key.app.secret
  sensitive = true
}

# Start of the APP_ENV secret; append POSTGRES_PASSWORD, HOST_CODE and ADMIN_TOKEN.
output "app_env" {
  sensitive = true
  value     = <<-EOT
    SITE_DOMAIN=${var.domain}
    POSTGRES_DB=app
    POSTGRES_USER=app
    S3_ENDPOINT=https://s3.ap-northeast-2.amazonaws.com
    S3_REGION=ap-northeast-2
    S3_BUCKET=${aws_s3_bucket.photos.id}
    S3_ACCESS_KEY=${aws_iam_access_key.app.id}
    S3_SECRET_KEY=${aws_iam_access_key.app.secret}
    S3_PATH_STYLE=false
  EOT
}
