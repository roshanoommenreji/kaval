#!/usr/bin/env bash
# Resume the production database: start the instance and wait until it's reachable.
#
# Paired with pause-database.sh. Called by `make up` before the app node comes back, so the
# database is already there by the time pods try to connect to it.
#
# This only gets the instance itself running and reachable over SSM. The full start-up health
# check ADR-0008 describes -- pg_isready plus a sanity query, restoring the latest pre-stop
# snapshot only on failure -- is still a deferred ROADMAP.md line
# (docs/runbooks/restore-from-backup.md has the manual version for now).

set -euo pipefail

export AWS_PROFILE="${AWS_PROFILE:-kaval}"
export AWS_REGION="${AWS_REGION:-ap-south-1}"

INSTANCE_ID=$(aws ec2 describe-instances \
  --filters "Name=tag:Role,Values=database" "Name=instance-state-name,Values=running,stopping,stopped,pending" \
  --query "Reservations[0].Instances[0].InstanceId" --output text)

if [ "$INSTANCE_ID" = "None" ] || [ -z "$INSTANCE_ID" ]; then
  echo "No database instance found -- nothing to resume (has 'terraform apply' created it yet?)."
  exit 0
fi

STATE=$(aws ec2 describe-instances --instance-ids "$INSTANCE_ID" \
  --query "Reservations[0].Instances[0].State.Name" --output text)

if [ "$STATE" = "running" ]; then
  echo "Database ($INSTANCE_ID) is already running."
  exit 0
fi

echo "Starting database instance $INSTANCE_ID..."
aws ec2 start-instances --instance-ids "$INSTANCE_ID" >/dev/null
aws ec2 wait instance-running --instance-ids "$INSTANCE_ID"

echo "Running -- waiting for SSM..."
until [ "$(aws ssm describe-instance-information \
    --filters "Key=InstanceIds,Values=$INSTANCE_ID" \
    --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null)" = "Online" ]; do
  sleep 5
done
echo "Database online."
