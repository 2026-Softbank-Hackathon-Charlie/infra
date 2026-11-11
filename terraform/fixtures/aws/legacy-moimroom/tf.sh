#!/usr/bin/env bash
# Runs Terraform from the official image for this root only.
#   ./tf.sh init | plan | apply | output ... | destroy
# Credentials come from the AWS CLI profile in AWS_PROFILE (default: hopcharlie)
# and must belong to the hackathon account.
set -euo pipefail
cd "$(dirname "$0")"

profile="${AWS_PROFILE:-hopcharlie}"
account="525091405886"
root="$(pwd)"
if command -v cygpath >/dev/null; then # Git Bash on Windows
  root="$(cygpath -w "$root")"
fi

got="$(aws sts get-caller-identity --profile "$profile" --query Account --output text)"
if [ "$got" != "$account" ]; then
  echo "tf.sh: profile '$profile' is account $got, not $account" >&2
  exit 1
fi

# Pass only this profile's credentials, not the whole ~/.aws.
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
eval "$(aws configure export-credentials --profile "$profile" --format env)"
export AWS_SESSION_TOKEN="${AWS_SESSION_TOKEN:-}"
who="$(whoami 2>/dev/null || echo user)-$(hostname 2>/dev/null || echo host)"

MSYS_NO_PATHCONV=1 exec docker run --rm -i --hostname "${who//[^A-Za-z0-9-]/-}" \
  -v "${root}:/terraform" -w /terraform \
  -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_SESSION_TOKEN \
  -e TF_IN_AUTOMATION=1 \
  hashicorp/terraform:1.17.1 "$@"
