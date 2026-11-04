#!/usr/bin/env bash
# Stores the workload cluster's Sealed Secrets public certificate as the SSM
# String parameter /hopcharlie/sealed-secrets/eks. The platform API's init
# container copies it to /config/sealed-secrets/eks.pem, and the platform
# seals app keys and secrets with it (ADR 0035, platform ADR 0016 and 0019).
#
# It reads only the public certificate, from the controller's /v1/cert.pem
# through the API server's service proxy, the same path as kubeseal
# --fetch-cert. It reads no Secret and prints nothing private.
#
# Needs the workload API tunnel in another terminal (README, private EKS access):
#   make aws-tunnel CLUSTER=workload
# then, with an AWS CLI profile of the hackathon account (default: hopcharlie):
#   AWS_PROFILE=<profile> bash scripts/put-sealed-secrets-cert.sh
set -euo pipefail
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1 # Git Bash: keep /api/... and /hopcharlie/... as they are

profile="${AWS_PROFILE:-hopcharlie}"
account="${HOPCHARLIE_AWS_ACCOUNT:-525091405886}"
region=ap-northeast-2
parameter=/hopcharlie/sealed-secrets/eks
kubeconfig=.generated/access/workload.kubeconfig # written by make aws-tunnel CLUSTER=workload
context=charlie-dev-workload
# The workload sealed-secrets addon: chart 2.20.0 in kube-system with
# fullnameOverride sealed-secrets-controller (clusters/aws-dev-workload/values/sealed-secrets.yaml).
cert_path=/api/v1/namespaces/kube-system/services/http:sealed-secrets-controller:http/proxy/v1/cert.pem

if [ ! -f "$kubeconfig" ]; then
  echo "put-sealed-secrets-cert.sh: $kubeconfig is missing; run make aws-tunnel CLUSTER=workload first" >&2
  exit 1
fi

got="$(aws sts get-caller-identity --profile "$profile" --query Account --output text)"
if [ "$got" != "$account" ]; then
  echo "put-sealed-secrets-cert.sh: profile '$profile' is account $got, not the hackathon account $account" >&2
  exit 1
fi

umask 077
mkdir -p .generated/sealed-secrets
cert=.generated/sealed-secrets/eks.pem
trap 'rm -f "$cert"' EXIT

kubectl --kubeconfig "$kubeconfig" --context "$context" get --raw "$cert_path" > "$cert"

# Exactly one certificate and nothing else.
if grep -q 'PRIVATE KEY' "$cert"; then
  echo "put-sealed-secrets-cert.sh: the response holds a private key; refusing to store it" >&2
  exit 1
fi
if [ "$(grep -c -- '-----BEGIN CERTIFICATE-----' "$cert")" != 1 ] || [ "$(grep -c -- '-----END CERTIFICATE-----' "$cert")" != 1 ]; then
  echo "put-sealed-secrets-cert.sh: the response is not one PEM certificate" >&2
  exit 1
fi
if command -v openssl >/dev/null; then
  openssl x509 -in "$cert" -noout -subject -enddate -fingerprint -sha256
fi

aws ssm put-parameter --profile "$profile" --region "$region" --overwrite \
  --name "$parameter" --type String --value "file://$cert" >/dev/null
echo "stored $parameter. The platform API reads it when it starts:"
echo "  kubectl --kubeconfig .generated/access/management.kubeconfig --context charlie-dev-management -n charlie-platform rollout restart deployment/hopcharlie-api"
