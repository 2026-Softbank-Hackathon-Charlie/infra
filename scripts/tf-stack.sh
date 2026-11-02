#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
action="${1:-}"
case "$action" in init|plan|apply) ;; *) fail '지원하는 동작: init, plan, apply' ;; esac
[[ "$#" == 1 ]] || fail '사용법: tf-stack.sh init|plan|apply'
stack="${STACK:-}"
dir="$(stack_directory "$stack")"
[[ ! -f "$dir/.retired" ]] || fail "$stack: retired; automatic recreation is disabled."
[[ ! -f "$dir/.scaffold" ]] || fail "$stack: scaffold입니다."
require_command python3
require_command terraform
work="$(mktemp -d "${TMPDIR:-/tmp}/charlie-inputs.XXXXXX")"
trap 'rm -rf "$work"' EXIT
python3 "$REPO_ROOT/scripts/project-config.py" prepare --stack "$stack" --output-dir "$work" > "$work/settings"
read -r expected_account expected_region expected_bucket < "$work/settings"
require_command aws
actual_account="$(aws sts get-caller-identity --query Account --output text)"
[[ "$actual_account" == "$expected_account" ]] || fail 'AWS 계정 불일치; 실행을 중단합니다.'
export AWS_REGION="$expected_region"
# Terraform detects backend changes itself; never force migration or reconfiguration.
terraform -chdir="$dir" init -input=false -lockfile=readonly -backend-config="$work/backend.json"
if [[ "$action" != init ]]; then
  # apply retains the operator's interactive approval.
  terraform -chdir="$dir" "$action" -var-file="$work/inputs.tfvars.json"
fi
