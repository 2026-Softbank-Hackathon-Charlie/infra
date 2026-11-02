#!/usr/bin/env bash
# CI deploys root stacks in dependency order. Account IAM is managed by an administrator.
# shellcheck source=scripts/common.sh
source "$(dirname "$0")/common.sh"

action="${1:-plan}"
case "$action" in
  plan|apply) ;;
  *) fail '지원하는 CI 동작: plan, apply' ;;
esac
[[ "$#" -le 2 ]] || fail '사용법: tf-ci.sh [plan|apply] [bootstrap|foundation|management|workload|all-runtime]'
target="${2:-bootstrap}"
case "$target" in
  bootstrap) stacks=(bootstrap/aws) ;;
  foundation) stacks=(aws/dev/foundation) ;;
  management) stacks=(aws/dev/management) ;;
  workload) stacks=(aws/dev/workload) ;;
  all-runtime) stacks=(bootstrap/aws aws/dev/foundation aws/dev/management aws/dev/workload) ;;
  *) fail '지원하지 않는 stack 선택입니다. account는 관리자 로컬 전용입니다.' ;;
esac

require_command terraform
require_command aws
require_command python3
enabled_stacks=()

# Check every enabled root before applying any resources.
for stack in "${stacks[@]}"; do
  dir="$(stack_directory "$stack")"
  [[ -d "$dir" ]] || fail "$stack: 디렉터리가 없습니다."
  if [[ -f "$dir/.retired" ]]; then
    printf 'skip: %s (retired)\n' "$stack"
    continue
  fi
  if [[ -f "$dir/.scaffold" ]]; then
    [[ "$stack" != bootstrap/aws ]] || fail 'bootstrap 리소스를 먼저 구현하세요.'
    printf 'skip: %s (scaffold)\n' "$stack"
    continue
  fi
  [[ -f "$dir/.terraform.lock.hcl" ]] || fail "$stack: .terraform.lock.hcl을 Git에 포함하세요."
  grep -Eq '^[[:space:]]*backend[[:space:]]+"s3"' "$dir/backend.tf" \
    || fail "$stack: S3 backend 선언이 필요합니다."
  enabled_stacks+=("$stack")
done

if [[ "${#enabled_stacks[@]}" == 0 ]]; then
  printf 'All AWS deployment stacks are retired.\n'
  exit 0
fi

work="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/charlie-ci-inputs.XXXXXX")"
plan_file=''
trap 'rm -rf "$work"; if [[ -n "$plan_file" ]]; then rm -f "$plan_file"; fi' EXIT
# Validate every root and input before the first AWS/Terraform command.
for stack in "${enabled_stacks[@]}"; do
  python3 "$REPO_ROOT/scripts/project-config.py" prepare --ci --stack "$stack" \
    --output-dir "$work/$stack" > "$work/settings"
done
read -r expected_account expected_region expected_bucket < "$work/settings"
actual_account="$(aws sts get-caller-identity --query Account --output text)"
[[ "$actual_account" == "$expected_account" ]] || fail 'AWS 계정 불일치; 실행을 중단합니다.'
export TF_IN_AUTOMATION=true TF_INPUT=false
export AWS_REGION="$expected_region"

for stack in "${enabled_stacks[@]}"; do
  dir="$(stack_directory "$stack")"
  printf '%s: %s\n' "$action" "$stack"
  terraform -chdir="$dir" init -input=false -lockfile=readonly \
    -backend-config="$work/$stack/backend.json"

  plan_file="$(mktemp "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/charlie-tfplan.XXXXXX")"
  terraform -chdir="$dir" plan -input=false -lock-timeout=5m -var-file="$work/$stack/inputs.tfvars.json" -out="$plan_file"
  if [[ "$stack" == aws/dev/foundation ]]; then
    terraform -chdir="$dir" show -json "$plan_file" | python3 "$REPO_ROOT/scripts/check-foundation-plan.py"
  fi
  if [[ "$action" == apply ]]; then
    terraform -chdir="$dir" apply -input=false -lock-timeout=5m "$plan_file"
  fi
  rm -f "$plan_file"
  plan_file=''
done
