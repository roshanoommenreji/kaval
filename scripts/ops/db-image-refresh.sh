#!/usr/bin/env bash
# Move a RUNNING database server onto the image pinned in git (KAV-169, ADR-0107). Run it after the
# server is up and before the app starts (docs/runbooks/prod-rollout.md, step 2). Needs Roshan's go,
# like every change to AWS: it restarts the database for a few seconds. Staging does not need it
# (staging's server is built from the start-up script each time).
#
#   AWS_PROFILE=kaval bash scripts/ops/db-image-refresh.sh              # production's server
#   DB_NAME_PREFIX=kaval-staging bash scripts/ops/db-image-refresh.sh   # staging's
#
# The pin is read from the start-up script, the one place the server's image is written, so there is
# nothing to type that could be wrong. Output is the image before and after and the server version.
set -euo pipefail
export AWS_PROFILE="${AWS_PROFILE:-kaval}"
export AWS_REGION="${AWS_REGION:-ap-south-1}"
HERE="$(cd "$(dirname "$0")" && pwd)"
export DB_NAME_PREFIX="${DB_NAME_PREFIX:-kaval-prod}"
PIN="$(grep -o 'pgvector/pgvector:[^ ]*@sha256:[0-9a-f]\{64\}' "$HERE/../../infra/modules/database/user_data.sh.tftpl" | head -1)"
[ -n "$PIN" ] || { echo "no pinned pgvector image in the start-up script"; exit 1; }

INSTANCE_ID=$(aws ec2 describe-instances \
  --filters "Name=tag:Role,Values=database" "Name=tag:Name,Values=${DB_NAME_PREFIX}-database" "Name=instance-state-name,Values=running" \
  --query "Reservations[0].Instances[0].InstanceId" --output text)
if [ "$INSTANCE_ID" = "None" ] || [ -z "$INSTANCE_ID" ]; then echo "No running ${DB_NAME_PREFIX}-database server."; exit 1; fi

echo "server ${DB_NAME_PREFIX}-database will run: $PIN"
if [ "${ASSUME_YES:-}" != "1" ]; then read -r -p "Type yes to restart its database now: " ans; [ "$ans" = "yes" ] || { echo "stopped"; exit 1; }; fi

SCRIPT_B64="$(base64 -w0 "$HERE/db-image-refresh-remote.sh" 2>/dev/null || base64 "$HERE/db-image-refresh-remote.sh" | tr -d '\n')"
CMD_ID=$(aws ssm send-command --instance-ids "$INSTANCE_ID" --document-name AWS-RunShellScript \
  --parameters "commands=[\"echo $SCRIPT_B64 | base64 -d > /tmp/db-image-refresh.sh\",\"PIN='$PIN' bash /tmp/db-image-refresh.sh; rm -f /tmp/db-image-refresh.sh\"]" \
  --query "Command.CommandId" --output text)
STATUS=Pending
for _ in $(seq 1 60); do
  sleep 3
  STATUS=$(aws ssm get-command-invocation --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" --query Status --output text 2>/dev/null || echo Pending)
  case "$STATUS" in Success|Failed|Cancelled|TimedOut) break;; esac
done
aws ssm get-command-invocation --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" --query '[StandardOutputContent,StandardErrorContent]' --output text | tail -20
[ "$STATUS" = "Success" ] || { echo "FAILED: the command ended as $STATUS. The old unit may still be in place; read the output above."; exit 1; }
bash "$HERE/health-check-database.sh"
