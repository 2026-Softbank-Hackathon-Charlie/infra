# A stand-in for a customer's existing app: one VM in the account's default VPC
# (outside the platform VPCs) running docker compose, plus an S3 photo bucket
# and a bucket-scoped IAM user whose keys the app reads from its .env.
locals {
  name   = "moimroom-legacy"
  bucket = "moimroom-legacy-photos-${data.aws_caller_identity.current.account_id}"
  # Tags an ordinary small company would put on its own app (provider
  # default_tags and the root volume). The onboarding discovery shows the
  # instance tags, so they name the app, not the team or the platform.
  # Teardown finds everything by this root's state and Name=moimroom-legacy.
  tags = {
    Project     = "moimroom"
    Environment = "production"
    Component   = "web"
    ManagedBy   = "Terraform"
  }
}

data "aws_caller_identity" "current" {}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnet" "public" {
  vpc_id            = data.aws_vpc.default.id
  availability_zone = var.availability_zone
  default_for_az    = true
}

# Canonical publishes this parameter; its AMIs ship the SSM agent (snap).
data "aws_ssm_parameter" "ubuntu" {
  name = "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"
}

resource "aws_security_group" "vm" {
  name        = local.name
  description = "HTTP and HTTPS from anywhere, SSH from the deploy runners only"
  vpc_id      = data.aws_vpc.default.id
  tags        = { Name = local.name }
}

resource "aws_vpc_security_group_ingress_rule" "web" {
  for_each          = toset(["80", "443"])
  security_group_id = aws_security_group.vm.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = tonumber(each.value)
  to_port           = tonumber(each.value)
}

resource "aws_vpc_security_group_ingress_rule" "ssh" {
  for_each          = toset(var.ssh_allowed_cidrs)
  security_group_id = aws_security_group.vm.id
  cidr_ipv4         = each.value
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
  description       = "deploy runner"
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.vm.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_iam_role" "vm" {
  name               = local.name
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "ec2.amazonaws.com" } }] })
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.vm.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "vm" {
  name = local.name
  role = aws_iam_role.vm.name
}

resource "aws_instance" "vm" {
  ami                    = data.aws_ssm_parameter.ubuntu.value
  instance_type          = "t3.small"
  subnet_id              = data.aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.vm.id]
  iam_instance_profile   = aws_iam_instance_profile.vm.name
  # Owner tags must be on RunInstances for the new volume as well.
  volume_tags = merge(local.tags, { Name = local.name })
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }
  root_block_device {
    volume_size           = 20
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
  }
  credit_specification { cpu_credits = "standard" }
  user_data  = templatefile("${path.module}/user-data.sh.tftpl", { deploy_ssh_public_key = trimspace(var.deploy_ssh_public_key) })
  tags       = { Name = local.name }
  depends_on = [aws_iam_role_policy_attachment.ssm]
  lifecycle {
    # A newer Ubuntu image or key change must not replace the VM and its data.
    ignore_changes = [ami, user_data]
  }
}

resource "aws_eip" "vm" {
  domain   = "vpc"
  instance = aws_instance.vm.id
  tags     = { Name = local.name }
}

resource "aws_s3_bucket" "photos" {
  bucket = local.bucket
  # Fixture data only: destroy empties the bucket.
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "photos" {
  bucket                  = aws_s3_bucket.photos.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "photos" {
  bucket = aws_s3_bucket.photos.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "photos" {
  bucket = aws_s3_bucket.photos.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_iam_user" "app" {
  name = "${local.name}-app"
  path = "/fixtures/"
}

resource "aws_iam_user_policy" "app" {
  name = "photo-bucket-only"
  user = aws_iam_user.app.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:ListBucket", "s3:GetBucketLocation"], Resource = aws_s3_bucket.photos.arn },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = "${aws_s3_bucket.photos.arn}/*" },
    ]
  })
}

# The secret ends up in this root's state (encrypted S3 bucket, team access only).
resource "aws_iam_access_key" "app" {
  user = aws_iam_user.app.name
}
