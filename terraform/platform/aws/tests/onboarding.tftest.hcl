mock_provider "aws" {
  override_during = plan
  mock_data "aws_caller_identity" { defaults = { account_id = "123456789012" } }
  mock_data "aws_subnets" { defaults = { ids = ["subnet-12345678"] } }
  mock_data "aws_ssm_parameter" { defaults = { insecure_value = "ami-12345678" } }
  mock_resource "aws_ssm_document" { defaults = { arn = "arn:aws:ssm:ap-northeast-2:123456789012:document/mock" } }
  mock_resource "aws_iam_policy" { defaults = { arn = "arn:aws:iam::123456789012:policy/mock" } }
}
mock_provider "aws" {
  alias           = "second"
  override_during = plan
  mock_resource "aws_ssm_document" { defaults = { arn = "arn:aws:ssm:ap-southeast-2:123456789012:document/mock" } }
}
mock_provider "random" {}

run "onboarding_documents_and_permissions" {
  command = plan
  assert {
    condition     = toset([for d in aws_ssm_document.onboarding : d.name]) == toset(["HopCharlie-Inspect", "HopCharlie-Connect", "HopCharlie-Disconnect"]) && toset([for d in aws_ssm_document.onboarding_second : d.name]) == toset([for d in aws_ssm_document.onboarding : d.name]) && alltrue([for k, d in aws_ssm_document.onboarding : aws_ssm_document.onboarding_second[k].content == d.content]) && alltrue([for d in aws_ssm_document.onboarding : d.document_type == "Command" && d.target_type == "/AWS::EC2::Instance"])
    error_message = "Exactly the three HopCharlie Command documents, the same in both regions."
  }
  assert {
    condition     = keys(jsondecode(aws_ssm_document.onboarding["Inspect"].content).parameters) == ["maxChunk", "publicKey"] && keys(jsondecode(aws_ssm_document.onboarding["Connect"].content).parameters) == ["appLabel", "dbContainer", "hostname", "network", "publicKey", "subnet", "tailscaleAuthKey"] && keys(jsondecode(aws_ssm_document.onboarding["Disconnect"].content).parameters) == ["appLabel", "dbContainer"] && [for s in jsondecode(aws_ssm_document.onboarding["Inspect"].content).mainSteps : s.name] == ["inspect", "chunk1", "chunk2", "chunk3", "chunk4", "chunk5", "chunk6", "chunk7", "chunk8", "chunk9", "cleanup"] && [for s in jsondecode(aws_ssm_document.onboarding["Connect"].content).mainSteps : s.name] == ["connect"] && [for s in jsondecode(aws_ssm_document.onboarding["Disconnect"].content).mainSteps : s.name] == ["disconnect"]
    error_message = "Document parameters must match the contract."
  }
  assert {
    condition     = alltrue(flatten([for d in aws_ssm_document.onboarding : [for p in values(jsondecode(d.content).parameters) : p.type == "String" && p.interpolationType == "ENV_VAR" && startswith(p.allowedPattern, "^") && endswith(p.allowedPattern, "$")]]))
    error_message = "Every parameter is a validated String passed as an environment variable."
  }
  assert {
    condition     = alltrue([for d in aws_ssm_document.onboarding : alltrue([for s in jsondecode(d.content).mainSteps : length(s.inputs.runCommand) == 1 && !strcontains(s.inputs.runCommand[0], "{{")])])
    error_message = "Every step is one command that SSM does not interpolate."
  }
  assert {
    condition     = alltrue([for k, f in { Inspect = "inspect.sh", Connect = "connect.sh", Disconnect = "disconnect.sh" } : strcontains(jsondecode(aws_ssm_document.onboarding[k].content).mainSteps[0].inputs.runCommand[0], filebase64("onboarding/${f}"))]) && strcontains(jsondecode(aws_ssm_document.onboarding["Inspect"].content).mainSteps[3].inputs.runCommand[0], "HC_MODE=chunk HC_CHUNK=3 HC_PUBLIC_KEY=\"$SSM_publicKey\"") && strcontains(jsondecode(aws_ssm_document.onboarding["Connect"].content).mainSteps[0].inputs.runCommand[0], "HC_TS_AUTHKEY=\"$SSM_tailscaleAuthKey\"")
    error_message = "Each document carries its script file."
  }
  assert {
    condition     = jsondecode(aws_ssm_document.onboarding["Inspect"].content).mainSteps[0].onFailure == "exit" && jsondecode(aws_ssm_document.onboarding["Inspect"].content).mainSteps[10].finallyStep == true && length([for s in jsondecode(aws_ssm_document.onboarding["Inspect"].content).mainSteps : s if can(s.onFailure) || can(s.finallyStep)]) == 2 && alltrue([for k in ["Connect", "Disconnect"] : !can(jsondecode(aws_ssm_document.onboarding[k].content).mainSteps[0].onFailure)])
    error_message = "Inspect stops after a failed inspection and always cleans up; Connect and Disconnect fail on any non-zero exit."
  }
  assert {
    condition     = toset([for s in jsondecode(aws_iam_policy.platform_onboarding.policy).Statement : s.Sid]) == toset(["Discover", "OnlyTheHopCharlieDocuments", "CommandsOnInstances", "OnlyTheOnboardingTag", "StopTaggedInstances", "NeverEksNodes", "NeverAutoScalingInstances", "NeverCommandsOnEksNodes", "NeverCommandsOnAutoScalingInstances", "NeverInfrastructureInstances", "NeverCommandsOnInfrastructureInstances"])
    error_message = "Onboarding statements must match the contract."
  }
  assert {
    condition     = alltrue([for s in jsondecode(aws_iam_policy.platform_onboarding.policy).Statement : s.Effect == "Deny" || length(setintersection(toset(flatten([s.Action])), toset(["ec2:TerminateInstances", "ssm:*", "ec2:*", "ssm:StartSession", "ec2:ModifyInstanceAttribute"]))) == 0])
    error_message = "No terminate, session or wildcard action."
  }
  assert {
    condition     = one([for s in jsondecode(aws_iam_policy.platform_onboarding.policy).Statement : s if s.Sid == "OnlyTheOnboardingTag"]).Condition["ForAllValues:StringEquals"]["aws:TagKeys"] == ["hopcharlie-onboarding"] && one([for s in jsondecode(aws_iam_policy.platform_onboarding.policy).Statement : s if s.Sid == "StopTaggedInstances"]).Condition.Null["aws:ResourceTag/hopcharlie-onboarding"] == "false"
    error_message = "Tags only with the onboarding key, stops only tagged instances."
  }
  assert {
    condition     = alltrue([for s in jsondecode(aws_iam_policy.platform_onboarding.policy).Statement : s.Resource == "*" || try(startswith(s.Resource[0], "arn:aws:ssm:"), false) || try(toset(s.Resource) == toset(["arn:aws:ec2:ap-northeast-2:123456789012:instance/*", "arn:aws:ec2:ap-southeast-2:123456789012:instance/*"]), false)]) && one([for s in jsondecode(aws_iam_policy.platform_onboarding.policy).Statement : s if s.Sid == "NeverCommandsOnInfrastructureInstances"]).Condition.StringEquals["ssm:resourceTag/Component"] == ["access", "app-private-network", "eks"] && one([for s in jsondecode(aws_iam_policy.platform_onboarding.policy).Statement : s if s.Sid == "NeverInfrastructureInstances"]).Condition.StringEquals["aws:ResourceTag/Component"] == ["access", "app-private-network", "eks"]
    error_message = "Instances only in the two regions, never the account's own infrastructure instances."
  }
  assert {
    condition     = one([for s in jsondecode(aws_iam_policy.platform_onboarding.policy).Statement : s if s.Sid == "OnlyTheHopCharlieDocuments"]).Action == "ssm:SendCommand" && length(one([for s in jsondecode(aws_iam_policy.platform_onboarding.policy).Statement : s if s.Sid == "OnlyTheHopCharlieDocuments"]).Resource) == 6 && aws_iam_role_policy_attachment.platform_onboarding.role == aws_iam_role.platform_pod.name
    error_message = "SendCommand names only the three documents in both regions, attached to the platform role."
  }
}
