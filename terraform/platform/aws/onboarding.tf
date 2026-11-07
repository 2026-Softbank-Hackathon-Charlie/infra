# Onboarding apps that ran on one VM with docker compose (platform ADR 0042,
# infra ADR 0082): the platform role finds the VM, runs the three reviewed
# SSM documents below on it, tags it and, after the cutover, stops it.
# Nothing else: no other document (AWS-RunShellScript), no terminate, and
# nothing on EKS nodes or Auto Scaling instances. The documents exist in both
# regions the account may use, since a source VM can be in either.
#
# Cost: none (SSM Command documents, IAM policies and Run Command are free).
# Cleanup: ./tf.sh destroy removes them with the rest of this stack.

locals {
  onboarding_tag = "hopcharlie-onboarding"
  instance_arn   = "arn:aws:ec2:*:${data.aws_caller_identity.me.account_id}:instance/*"

  # The patterns repeat the scripts' own checks (terraform/platform/aws/
  # onboarding/*.sh), so SendCommand refuses what the script would.
  public_key_rule = "^[A-Za-z0-9+/]{198,1200}={0,2}$" # base64 DER of the one-time RSA key, 200..1200 characters
  docker_name     = "^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"
  app_label       = "^[a-z0-9]([a-z0-9-]{0,38}[a-z0-9])?$"

  # Each document carries its script base64-encoded, so SSM interpolates
  # nothing in it, and every parameter reaches it only as SSM_<name>
  # (interpolationType ENV_VAR), which each step maps to the script's HC_*
  # variables. The first step writes the script to /run once; later steps of
  # the same command run it with their own mode.
  onboarding_documents = {
    Inspect = {
      script      = "inspect.sh"
      description = "HopCharlie onboarding: read the compose project, containers and database of this VM (platform ADR 0042). Values leave encrypted with publicKey, in chunks of maxChunk characters."
      timeout     = 600
      parameters = {
        publicKey = { description = "Base64 DER of the inspection's one-time RSA public key.", allowedPattern = local.public_key_rule }
        maxChunk  = { description = "Characters per step output, 1000..24000.", allowedPattern = "^([1-9][0-9]{3}|1[0-9]{4}|2[0-3][0-9]{3}|24000)$", default = "20000" }
      }
      env = "HC_PUBLIC_KEY=\"$SSM_publicKey\" HC_MAX_CHUNK=\"$SSM_maxChunk\" HC_MAX_CHUNKS=10"
      steps = concat(
        [{ name = "inspect", mode = "HC_MODE=run" }],
        [for i in range(1, 10) : { name = "chunk${i}", mode = "HC_MODE=chunk HC_CHUNK=${i}" }],
        [{ name = "cleanup", mode = "HC_MODE=clean" }],
      )
    }
    Connect = {
      script      = "connect.sh"
      description = "HopCharlie onboarding: replication account, hba line, wal_keep_size and the Tailscale container next to the source database (platform ADR 0042)."
      timeout     = 900
      parameters = {
        dbContainer      = { description = "The postgres container.", allowedPattern = local.docker_name }
        network          = { description = "The compose network of the database container.", allowedPattern = local.docker_name }
        subnet           = { description = "That network's IPv4 subnet, for the hba line.", allowedPattern = "^[0-9]{1,3}(\\.[0-9]{1,3}){3}/[0-9]{1,2}$" }
        tailscaleAuthKey = { description = "One-time, preauthorized, tag:hop-legacy auth key that expires in an hour.", allowedPattern = "^tskey-auth-[A-Za-z0-9_-]{10,200}$" }
        hostname         = { description = "Tailnet hostname, hop-db-legacy-<appLabel>.", allowedPattern = "^hop-db-legacy-[a-z0-9]([a-z0-9-]{0,38}[a-z0-9])?$" }
        publicKey        = { description = "Base64 DER of the one-time RSA public key the replication password is encrypted with.", allowedPattern = local.public_key_rule }
        appLabel         = { description = "The app's name.", allowedPattern = local.app_label }
      }
      env   = "HC_DB_CONTAINER=\"$SSM_dbContainer\" HC_NETWORK=\"$SSM_network\" HC_SUBNET=\"$SSM_subnet\" HC_TS_AUTHKEY=\"$SSM_tailscaleAuthKey\" HC_HOSTNAME=\"$SSM_hostname\" HC_PUBLIC_KEY=\"$SSM_publicKey\" HC_APP=\"$SSM_appLabel\""
      steps = [{ name = "connect", mode = "" }]
    }
    Disconnect = {
      script      = "disconnect.sh"
      description = "HopCharlie onboarding: undo Connect on this VM (Tailscale container, hba and wal_keep_size lines, replication account) (platform ADR 0042)."
      timeout     = 600
      parameters = {
        dbContainer = { description = "The postgres container.", allowedPattern = local.docker_name }
        appLabel    = { description = "The app's name.", allowedPattern = local.app_label }
      }
      env   = "HC_DB_CONTAINER=\"$SSM_dbContainer\" HC_APP=\"$SSM_appLabel\""
      steps = [{ name = "disconnect", mode = "" }]
    }
  }

  onboarding_document_content = { for k, d in local.onboarding_documents : k => jsonencode({
    schemaVersion = "2.2"
    description   = d.description
    parameters = { for n, p in d.parameters : n => merge(
      { type = "String", description = p.description, allowedPattern = p.allowedPattern, interpolationType = "ENV_VAR" },
      try({ default = p.default }, {})
    ) }
    mainSteps = [for i, s in d.steps : merge(
      # Inspect: no chunk is read after a failed inspection, and cleanup runs
      # whatever happened before it. Connect and Disconnect fail on any
      # non-zero exit of their one step (Disconnect 4: the database is stopped).
      { for k, v in { onFailure = "exit" } : k => v if s.name == "inspect" },
      { for k, v in { finallyStep = true } : k => v if s.name == "cleanup" },
      {
        action       = "aws:runShellScript"
        name         = s.name
        precondition = { StringEquals = ["platformType", "Linux"] }
        inputs = {
          timeoutSeconds = d.timeout
          runCommand = [join("; ", compact([
            "f=/run/hopcharlie-onboarding-doc/${trimsuffix(d.script, ".sh")}-${substr(filesha256("${path.module}/onboarding/${d.script}"), 0, 16)}.sh",
            i == 0 ? "mkdir -p -m 700 /run/hopcharlie-onboarding-doc && echo '${filebase64("${path.module}/onboarding/${d.script}")}' | base64 -d > \"$f.tmp\" && mv \"$f.tmp\" \"$f\" || exit 1" : "",
            "[ -f \"$f\" ] || { echo \"the ${d.script} of this command is missing\" >&2; exit 1; }",
            i == length(d.steps) - 1 ? "trap 'rm -f \"$f\"' EXIT" : "",
            "${s.mode == "" ? "" : "${s.mode} "}${d.env} bash \"$f\"",
          ]))]
        }
    })]
  }) }
}

resource "aws_ssm_document" "onboarding" {
  for_each        = local.onboarding_document_content
  name            = "HopCharlie-${each.key}"
  document_type   = "Command"
  document_format = "JSON"
  target_type     = "/AWS::EC2::Instance"
  content         = each.value
}

resource "aws_ssm_document" "onboarding_second" {
  provider        = aws.second
  for_each        = local.onboarding_document_content
  name            = "HopCharlie-${each.key}"
  document_type   = "Command"
  document_format = "JSON"
  target_type     = "/AWS::EC2::Instance"
  content         = each.value
}

resource "aws_iam_policy" "platform_onboarding" {
  name        = "${local.name}-platform-onboarding"
  description = "Onboarding of compose apps (infra ADR 0082): find the VM, run the three HopCharlie documents, tag and stop it."
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "Discover"
        Effect   = "Allow"
        Action   = ["ec2:DescribeInstances", "ec2:DescribeAddresses", "ec2:DescribeNetworkInterfaces", "ssm:DescribeInstanceInformation", "ssm:GetCommandInvocation"]
        Resource = "*"
      },
      {
        Sid      = "OnlyTheHopCharlieDocuments"
        Effect   = "Allow"
        Action   = "ssm:SendCommand"
        Resource = concat([for d in aws_ssm_document.onboarding : d.arn], [for d in aws_ssm_document.onboarding_second : d.arn])
      },
      # SendCommand authorizes the document and each instance separately, and
      # IAM cannot tie one to the other: Inspect needs every instance, so this
      # statement also lets Connect and Disconnect reach an untagged one. The
      # deny below and the fixed scripts are the boundary; the tag statement
      # states the platform's promise and holds alone without this one.
      {
        Sid      = "InspectAnyInstance"
        Effect   = "Allow"
        Action   = "ssm:SendCommand"
        Resource = local.instance_arn
      },
      {
        Sid       = "ConnectTaggedInstances"
        Effect    = "Allow"
        Action    = "ssm:SendCommand"
        Resource  = local.instance_arn
        Condition = { Null = { "ssm:resourceTag/${local.onboarding_tag}" = "false" } }
      },
      {
        Sid      = "OnlyTheOnboardingTag"
        Effect   = "Allow"
        Action   = ["ec2:CreateTags", "ec2:DeleteTags"]
        Resource = local.instance_arn
        Condition = {
          "ForAllValues:StringEquals" = { "aws:TagKeys" = [local.onboarding_tag] }
          Null                        = { "aws:TagKeys" = "false" }
        }
      },
      {
        Sid       = "StopTaggedInstances"
        Effect    = "Allow"
        Action    = "ec2:StopInstances"
        Resource  = local.instance_arn
        Condition = { Null = { "aws:ResourceTag/${local.onboarding_tag}" = "false" } }
      },
      {
        Sid       = "NeverEksNodes"
        Effect    = "Deny"
        Action    = ["ec2:CreateTags", "ec2:DeleteTags", "ec2:StopInstances"]
        Resource  = local.instance_arn
        Condition = { Null = { "aws:ResourceTag/eks:cluster-name" = "false" } }
      },
      {
        Sid       = "NeverAutoScalingInstances"
        Effect    = "Deny"
        Action    = ["ec2:CreateTags", "ec2:DeleteTags", "ec2:StopInstances"]
        Resource  = local.instance_arn
        Condition = { Null = { "aws:ResourceTag/aws:autoscaling:groupName" = "false" } }
      },
      {
        Sid       = "NeverCommandsOnEksNodes"
        Effect    = "Deny"
        Action    = "ssm:SendCommand"
        Resource  = local.instance_arn
        Condition = { Null = { "ssm:resourceTag/eks:cluster-name" = "false" } }
      },
      {
        Sid       = "NeverCommandsOnAutoScalingInstances"
        Effect    = "Deny"
        Action    = "ssm:SendCommand"
        Resource  = local.instance_arn
        Condition = { Null = { "ssm:resourceTag/aws:autoscaling:groupName" = "false" } }
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "platform_onboarding" {
  role       = aws_iam_role.platform_pod.name
  policy_arn = aws_iam_policy.platform_onboarding.arn
}
