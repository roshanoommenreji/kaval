#!/usr/bin/env bash
# Nightly production database dump to S3.
#
# Invoked by a CronJob in the Helm chart. Also runnable by hand before
# anything risky -- a migration, a manual fix, a Postgres upgrade.
#
# RPO is 24 hours, and that is a decision rather than a default. See
# docs/adr/0005-data-durability-and-staging-seeding.md for what was
# rejected and why, and for the trigger to revisit it.

set -euo pipefail

: "${POSTGRES_HOST:?}" "${POSTGRES_DB:?}" "${POSTGRES_USER:?}"
: "${BACKUP_BUCKET:?set BACKUP_BUCKET}"

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
KEY="postgres/${POSTGRES_DB}-${STAMP}.dump"
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT

echo "  dumping ${POSTGRES_DB}..."
# -Fc is the custom format: compressed, and pg_restore can filter it.
pg_dump -h "$POSTGRES_HOST" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f "$TMP"

SIZE=$(du -h "$TMP" | cut -f1)
echo "  ${SIZE} → s3://${BACKUP_BUCKET}/${KEY}"

aws s3 cp "$TMP" "s3://${BACKUP_BUCKET}/${KEY}" --only-show-errors

# A stable pointer, so restore.sh never has to list and sort the bucket.
echo "$KEY" | aws s3 cp - "s3://${BACKUP_BUCKET}/postgres/LATEST" --only-show-errors

# Keep 14 days. Storage is pennies, but unbounded growth is how a $0.05
# line item quietly becomes a $5 one.
CUTOFF=$(date -u -d '14 days ago' +%Y%m%d 2>/dev/null || date -u -v-14d +%Y%m%d)
aws s3 ls "s3://${BACKUP_BUCKET}/postgres/" \
  | awk '{print $4}' \
  | grep -E "^${POSTGRES_DB}-[0-9]{8}T" \
  | while read -r old; do
      d=$(echo "$old" | sed -E "s/^${POSTGRES_DB}-([0-9]{8})T.*/\1/")
      if [[ "$d" < "$CUTOFF" ]]; then
        echo "  expiring $old"
        aws s3 rm "s3://${BACKUP_BUCKET}/postgres/${old}" --only-show-errors
      fi
    done

echo "  done. A backup is not verified until you have restored one —"
echo "  which 'make staging-up' now does on every release."
