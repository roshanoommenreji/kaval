#!/usr/bin/env bash
# Restore the production database's data volume from an EBS snapshot -- whole-volume damage,
# option A in docs/runbooks/restore-from-backup.md. Never run automatically: the start-up
# health check (health-check-database.sh) only ever prints this command, it doesn't call it.
#
# Stops Postgres, creates a new volume from the snapshot in the same AZ, detaches the current
# volume (tagged Reason=damaged, kept -- never deleted, the same prevent_destroy posture as the
# Terraform-managed original), attaches and mounts the new one, restarts Postgres. WAL crash
# recovery running on first start is expected, not a failure.
#
# SNAPSHOT=<snap-id> picks the recovery point. Omit it to use the newest Role=database-data
# snapshot (pre-stop or daily DLM, whichever is newer) -- the same ordering the runbook's
# `describe-snapshots` query shows.

set -euo pipefail

export AWS_PROFILE="${AWS_PROFILE:-kaval}"
export AWS_REGION="${AWS_REGION:-ap-south-1}"
TF_PROD="${TF_PROD:-infra/envs/prod}"
# Git Bash on Windows rewrites a bare /dev/... argument into a Windows path before the AWS CLI
# sees it (MSYS path conversion) -- found live when --device /dev/sdf below got mangled into
# "C:/Program Files/Git/dev/sdf" and the attach failed. Harmless to set on every other shell.
export MSYS_NO_PATHCONV=1

INSTANCE_ID=$(aws ec2 describe-instances \
  --filters "Name=tag:Role,Values=database" "Name=instance-state-name,Values=running,stopped" \
  --query "Reservations[0].Instances[0].InstanceId" --output text)
if [ "$INSTANCE_ID" = "None" ] || [ -z "$INSTANCE_ID" ]; then
  echo "No database instance found."
  exit 1
fi

AZ=$(aws ec2 describe-instances --instance-ids "$INSTANCE_ID" \
  --query "Reservations[0].Instances[0].Placement.AvailabilityZone" --output text)

OLD_VOLUME_ID=$(aws ec2 describe-volumes \
  --filters "Name=attachment.instance-id,Values=$INSTANCE_ID" "Name=tag:Role,Values=database-data" \
  --query "Volumes[0].VolumeId" --output text)
if [ "$OLD_VOLUME_ID" = "None" ] || [ -z "$OLD_VOLUME_ID" ]; then
  echo "No attached database-data volume found on $INSTANCE_ID."
  exit 1
fi

if [ -z "${SNAPSHOT:-}" ]; then
  SNAPSHOT=$(aws ec2 describe-snapshots --owner-ids self \
    --filters "Name=tag:Project,Values=kaval" "Name=tag:Role,Values=database-data" \
    --query "reverse(sort_by(Snapshots,&StartTime))[0].SnapshotId" --output text)
  if [ "$SNAPSHOT" = "None" ] || [ -z "$SNAPSHOT" ]; then
    echo "No database-data snapshot found to restore from."
    exit 1
  fi
  echo "SNAPSHOT not given -- using the newest one: $SNAPSHOT"
fi

echo "Instance:        $INSTANCE_ID ($AZ)"
echo "Current volume:  $OLD_VOLUME_ID  (will be detached, tagged Reason=damaged, kept)"
echo "Restoring from:  $SNAPSHOT"
read -r -p "This stops Postgres and swaps the data volume. Continue? [y/N] " ok
if [ "$ok" != "y" ]; then
  echo "Aborted."
  exit 1
fi

echo "Stopping Postgres and unmounting /data..."
CMD_ID=$(aws ssm send-command --instance-ids "$INSTANCE_ID" --document-name "AWS-RunShellScript" \
  --parameters 'commands=["systemctl stop kaval-postgres","umount /data"]' \
  --query "Command.CommandId" --output text)
aws ssm wait command-executed --command-id "$CMD_ID" --instance-id "$INSTANCE_ID"

echo "Detaching $OLD_VOLUME_ID..."
aws ec2 detach-volume --volume-id "$OLD_VOLUME_ID" >/dev/null
aws ec2 wait volume-available --volume-ids "$OLD_VOLUME_ID"
aws ec2 create-tags --resources "$OLD_VOLUME_ID" --tags Key=Reason,Value=damaged
echo "  Detached and tagged. Not deleted -- verify the restore before removing it by hand."

NAME_PREFIX=$(aws ec2 describe-volumes --volume-ids "$OLD_VOLUME_ID" \
  --query "Volumes[0].Tags[?Key=='Name']|[0].Value" --output text | sed 's/-database-data$//')

echo "Creating a new volume from $SNAPSHOT..."
NEW_VOLUME_ID=$(aws ec2 create-volume --snapshot-id "$SNAPSHOT" --availability-zone "$AZ" \
  --volume-type gp3 \
  --tag-specifications "ResourceType=volume,Tags=[{Key=Name,Value=${NAME_PREFIX}-database-data},{Key=Project,Value=kaval},{Key=Role,Value=database-data}]" \
  --query "VolumeId" --output text)
aws ec2 wait volume-available --volume-ids "$NEW_VOLUME_ID"
echo "  $NEW_VOLUME_ID created."

echo "Attaching $NEW_VOLUME_ID to $INSTANCE_ID..."
aws ec2 attach-volume --volume-id "$NEW_VOLUME_ID" --instance-id "$INSTANCE_ID" --device /dev/sdf >/dev/null
aws ec2 wait volume-in-use --volume-ids "$NEW_VOLUME_ID"

echo "Mounting $NEW_VOLUME_ID and starting Postgres..."
# Same by-id lookup as user_data.sh.tftpl: Nitro instances attach EBS volumes as NVMe devices,
# not under the requested /dev/sdf, and the by-id symlink is the stable way to find them.
VOL_SUFFIX=$(echo "$NEW_VOLUME_ID" | tr -d '-')
DEV_PATH="/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_${VOL_SUFFIX}"
# No quotes around $DEV, and the sed expression uses single quotes with $DEV_PATH already
# interpolated locally (not a remote-side variable): the AWS CLI's --parameters shorthand
# parser (found live, Lab 27) chokes on any double-quote nested inside the commands=[...]
# string, so this whole command must contain zero literal double quotes.
#
# Also found live (this drill, Lab 28): the mount above only ever applied for the current
# boot. /etc/fstab still pointed at whichever volume was mounted *before* this restore, so
# the next stop/start silently (nofail) mounted nothing, and Postgres auto-initialised an
# empty cluster on the root disk instead of the restored data. The sed call replaces that
# stale /data line in place, so the next boot resolves the right device too.
MOUNT_CMD="DEV=${DEV_PATH}; for i in \$(seq 1 30); do [ -e \$DEV ] && break; sleep 2; done; sed -i 's#^.* /data ext4.*#${DEV_PATH} /data ext4 defaults,nofail 0 2#' /etc/fstab; mount \$DEV /data; systemctl start kaval-postgres"
CMD_ID=$(aws ssm send-command --instance-ids "$INSTANCE_ID" --document-name "AWS-RunShellScript" \
  --parameters "commands=[\"$MOUNT_CMD\"]" --query "Command.CommandId" --output text)
aws ssm wait command-executed --command-id "$CMD_ID" --instance-id "$INSTANCE_ID"

echo "Reconciling Terraform state so 'terraform plan' doesn't try to reattach $OLD_VOLUME_ID..."
if terraform -chdir="$TF_PROD" state rm module.database.aws_ebs_volume.data >/dev/null 2>&1 \
  && terraform -chdir="$TF_PROD" import module.database.aws_ebs_volume.data "$NEW_VOLUME_ID" >/dev/null 2>&1 \
  && terraform -chdir="$TF_PROD" state rm module.database.aws_volume_attachment.data >/dev/null 2>&1 \
  && terraform -chdir="$TF_PROD" import module.database.aws_volume_attachment.data "/dev/sdf:$NEW_VOLUME_ID:$INSTANCE_ID" >/dev/null 2>&1; then
  echo "  Done -- Terraform now tracks $NEW_VOLUME_ID. Run 'terraform plan' in $TF_PROD to confirm no drift."
else
  echo "  WARNING: state reconciliation failed partway. 'terraform plan' in $TF_PROD will likely"
  echo "  want to detach $NEW_VOLUME_ID and reattach the damaged $OLD_VOLUME_ID -- do not apply that."
  echo "  Fix by hand:"
  echo "    terraform -chdir=$TF_PROD state rm module.database.aws_ebs_volume.data module.database.aws_volume_attachment.data"
  echo "    terraform -chdir=$TF_PROD import module.database.aws_ebs_volume.data $NEW_VOLUME_ID"
  echo "    terraform -chdir=$TF_PROD import module.database.aws_volume_attachment.data /dev/sdf:$NEW_VOLUME_ID:$INSTANCE_ID"
fi

echo "Verifying..."
"$(dirname "$0")/health-check-database.sh"

echo "Restore complete. $OLD_VOLUME_ID stays detached and tagged Reason=damaged -- delete it by hand once you've checked row counts and max(ts) against what the runbook's 'Assess what you will lose' step recorded."
