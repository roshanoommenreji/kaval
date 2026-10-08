#!/usr/bin/env bash
# Pause the production database: snapshot its data volume, then stop the instance.
#
# Paired with resume-database.sh. Called by `make down` so the app node and the database
# pause together, as ADR-0008 actually says they should -- before this script existed,
# `make down` only destroyed the app node and left the database running, billing the full
# ~$14.40/mo regardless of whether anyone was using the system.
#
# Stop, never terminate: disable_api_termination on the instance and prevent_destroy on the
# data volume make that true even by accident. This script only ever calls stop-instances.
# The snapshot is the extra safety margin ADR-0008 asks for -- the daily DLM snapshot could
# be up to 24h stale; this one is taken seconds before the stop.

set -euo pipefail

export AWS_PROFILE="${AWS_PROFILE:-kaval}"
export AWS_REGION="${AWS_REGION:-ap-south-1}"

# Exact Name, not just Role=database: prod and staging each have a database server in this
# account (KAV-57), and "first match" would otherwise pick whichever the API lists first.
DB_NAME_PREFIX="${DB_NAME_PREFIX:-kaval-prod}"
INSTANCE_ID=$(aws ec2 describe-instances \
  --filters "Name=tag:Role,Values=database" "Name=tag:Name,Values=${DB_NAME_PREFIX}-database" "Name=instance-state-name,Values=running,stopping,stopped,pending" \
  --query "Reservations[0].Instances[0].InstanceId" --output text)

if [ "$INSTANCE_ID" = "None" ] || [ -z "$INSTANCE_ID" ]; then
  echo "No database instance found -- nothing to pause."
  exit 0
fi

STATE=$(aws ec2 describe-instances --instance-ids "$INSTANCE_ID" \
  --query "Reservations[0].Instances[0].State.Name" --output text)

if [ "$STATE" != "running" ]; then
  echo "Database ($INSTANCE_ID) is already $STATE -- nothing to pause."
  exit 0
fi

VOLUME_ID=$(aws ec2 describe-volumes \
  --filters "Name=attachment.instance-id,Values=$INSTANCE_ID" "Name=tag:Role,Values=database-data" \
  --query "Volumes[0].VolumeId" --output text)

if [ "$VOLUME_ID" != "None" ] && [ -n "$VOLUME_ID" ]; then
  echo "Snapshotting data volume $VOLUME_ID before stopping $INSTANCE_ID..."
  SNAPSHOT_ID=$(aws ec2 create-snapshot \
    --volume-id "$VOLUME_ID" \
    --description "kaval database pre-stop $(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --tag-specifications 'ResourceType=snapshot,Tags=[{Key=Name,Value='"${DB_NAME_PREFIX}"'-database-pre-stop},{Key=Reason,Value=pre-stop},{Key=Project,Value=kaval},{Key=Role,Value=database-data}]' \
    --query "SnapshotId" --output text)
  echo "  $SNAPSHOT_ID requested."
  echo "  Snapshots are async copy-on-write -- stopping the instance right after is safe;"
  echo "  AWS already has the volume's point-in-time state, not a live stream of it."
else
  echo "WARNING: no database-data volume found attached to $INSTANCE_ID -- stopping without a snapshot."
fi

echo "Stopping database instance $INSTANCE_ID..."
aws ec2 stop-instances --instance-ids "$INSTANCE_ID" >/dev/null
aws ec2 wait instance-stopped --instance-ids "$INSTANCE_ID"
echo "Database stopped. 'make up' (or resume-database.sh) brings it back."
