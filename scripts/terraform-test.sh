#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
require_command terraform
require_command python3
exec python3 "$REPO_ROOT/scripts/terraform-verify.py" --mode test
