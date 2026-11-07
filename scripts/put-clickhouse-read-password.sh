#!/usr/bin/env bash
# Copies the password of ClickStack's read-only ClickHouse user (app) from the
# management cluster's Secret observability/clickstack-clickhouse-auth (key
# read-password) into the SSM SecureString parameter /hopcharlie/clickhouse-read-password.
# The platform API's init container copies it to /config/aiobs-clickhouse-password
# and GET /v1/ai-activity reads hopai's log lines with it (infra ADR 0077).
#
# The value goes from the Secret to SSM through a 0600 file and is never printed
# or put on a command line. The file is removed on exit. Run it yourself: it
# stores a credential, which an agent does not do.
#
# Needs the management API tunnel in another terminal (README, private EKS access):
#   make aws-tunnel CLUSTER=management
# then, with an AWS CLI profile of the hackathon account (default: hopcharlie):
#   AWS_PROFILE=<profile> bash scripts/put-clickhouse-read-password.sh
#
# What it reads:  one key of one Secret (read-password) in observability.
# What it writes: one SSM SecureString parameter, overwritten if it exists.
set -euo pipefail
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1 # Git Bash: keep /hopcharlie/... as it is

profile="${AWS_PROFILE:-hopcharlie}"
account="${HOPCHARLIE_AWS_ACCOUNT:-525091405886}"
region=ap-northeast-2
parameter=/hopcharlie/clickhouse-read-password
kubeconfig=.generated/access/management.kubeconfig # written by make aws-tunnel CLUSTER=management

if [ ! -f "$kubeconfig" ]; then
  echo "put-clickhouse-read-password.sh: $kubeconfig is missing; run make aws-tunnel CLUSTER=management first" >&2
  exit 1
fi

got="$(aws sts get-caller-identity --profile "$profile" --query Account --output text)"
if [ "$got" != "$account" ]; then
  echo "put-clickhouse-read-password.sh: profile '$profile' is account $got, not the hackathon account $account" >&2
  exit 1
fi

umask 077
mkdir -p .generated/clickhouse
pw=.generated/clickhouse/read-password
trap 'rm -f "$pw"' EXIT

kubectl --kubeconfig "$kubeconfig" -n observability get secret clickstack-clickhouse-auth \
  -o 'jsonpath={.data.read-password}' | base64 -d > "$pw"

if [ ! -s "$pw" ]; then
  echo "put-clickhouse-read-password.sh: the Secret has no read-password" >&2
  exit 1
fi
echo "read-password: $(wc -c < "$pw" | tr -d ' ') bytes (not shown)"

aws ssm put-parameter --profile "$profile" --region "$region" --overwrite \
  --name "$parameter" --type SecureString --value "file://$pw" \
  --description "Read-only ClickHouse user app, for GET /v1/ai-activity (infra ADR 0077)" \
  --query Version --output text | sed 's/^/stored /; s/$/ (version)/'

echo "Stored $parameter. Restart hopcharlie-api so its init container fetches it:"
echo "  kubectl --kubeconfig $kubeconfig -n charlie-platform rollout restart deploy/hopcharlie-api"
