#!/usr/bin/env bash
# Start-up health check for the production database: pg_isready plus a sanity query, run
# remotely over SSM -- there is no network path to 5432 from the operator's machine, only the
# app node's security group can reach it (ADR-0008).
#
# Exit 0 means healthy. Exit 1 means the caller should stop and decide whether to restore.
# This script never restores anything itself: a flaky SSM command shouldn't be able to trigger
# a destructive-ish volume swap on its own. On failure it prints the exact
# `make db-restore-snapshot` command and leaves the decision to whoever's running `make up`.

set -euo pipefail

export AWS_PROFILE="${AWS_PROFILE:-kaval}"
export AWS_REGION="${AWS_REGION:-ap-south-1}"

INSTANCE_ID=$(aws ec2 describe-instances \
  --filters "Name=tag:Role,Values=database" "Name=instance-state-name,Values=running" \
  --query "Reservations[0].Instances[0].InstanceId" --output text)

if [ "$INSTANCE_ID" = "None" ] || [ -z "$INSTANCE_ID" ]; then
  echo "No running database instance found -- nothing to check."
  exit 1
fi

CMD_ID=$(aws ssm send-command \
  --instance-ids "$INSTANCE_ID" \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=["docker exec postgres pg_isready -U kaval -d kaval","docker exec postgres psql -U kaval -d kaval -t -c \"SELECT 1\""]' \
  --query "Command.CommandId" --output text)

# get-command-invocation can 404 for the first second or so after send-command -- that's the
# invocation record not existing yet, not a failure. Poll instead of a fixed sleep.
STATUS="Pending"
for _ in $(seq 1 15); do
  sleep 2
  STATUS=$(aws ssm get-command-invocation --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" \
    --query "Status" --output text 2>/dev/null || echo "Pending")
  case "$STATUS" in
    Success|Failed|Cancelled|TimedOut) break ;;
  esac
done

if [ "$STATUS" = "Success" ]; then
  echo "Database health check passed (pg_isready + SELECT 1)."
  exit 0
fi

echo "Database health check FAILED (SSM command status: $STATUS)."
echo "--- stderr ---"
aws ssm get-command-invocation --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" \
  --query "StandardErrorContent" --output text 2>/dev/null || true
echo "--------------"
echo "This does NOT necessarily mean the data is gone -- see docs/runbooks/restore-from-backup.md"
echo "to diagnose first. If a restore turns out to be the right call:"
echo "  make db-restore-snapshot              # restores from the newest snapshot"
echo "  make db-restore-snapshot SNAPSHOT=<id> # restores from a specific one"
exit 1
