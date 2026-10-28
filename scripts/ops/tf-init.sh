#!/usr/bin/env bash
# Initialise one Terraform environment against its remote state (KAV-86, ADR-0058).
#
#   scripts/ops/tf-init.sh <env> [extra terraform-init arguments]
#   env: prod | staging | dev | state | lab-eks
#
# Every environment's record of what exists lives in one private S3 bucket. Its name is not in
# git (public repo); it is in Parameter Store at /kaval/tfstate/bucket, which this script reads.
# On a new laptop this is the whole recovery: AWS login, then this, then `terraform plan`.
# The object key and lock setting are in each environment's own backend block.
#
# KAVAL_TFSTATE_BUCKET overrides the lookup (use it if Parameter Store is unreachable).
set -euo pipefail
export MSYS_NO_PATHCONV=1 PYTHONUTF8=1 PYTHONIOENCODING=utf-8   # Git Bash rewrites "/kaval/..." otherwise

ENV_NAME="${1:-}"
case "$ENV_NAME" in
  prod | staging | dev | state | lab-eks) shift ;;
  *) echo "usage: $0 <prod|staging|dev|state|lab-eks> [terraform init args]" >&2; exit 2 ;;
esac

ROOT="$(cd "$(dirname "$0")/../.." && { pwd -W 2>/dev/null || pwd; })"   # pwd -W: a path Windows Terraform understands
export AWS_PROFILE="${AWS_PROFILE:-kaval}"

BUCKET="${KAVAL_TFSTATE_BUCKET:-}"
if [ -z "$BUCKET" ]; then
  BUCKET="$(aws ssm get-parameter --name /kaval/tfstate/bucket --query Parameter.Value --output text 2>/dev/null || true)"
fi
if [ -z "$BUCKET" ] || [ "$BUCKET" = "None" ]; then
  echo "Could not read the state bucket's name from Parameter Store (/kaval/tfstate/bucket)." >&2
  echo "Check the AWS login (AWS_PROFILE=$AWS_PROFILE), or set KAVAL_TFSTATE_BUCKET." >&2
  exit 1
fi

exec terraform -chdir="$ROOT/infra/envs/$ENV_NAME" init -input=false -backend-config="bucket=$BUCKET" "$@"
