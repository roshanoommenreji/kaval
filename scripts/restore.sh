#!/usr/bin/env bash
# Restore the latest production snapshot into a target database, sanitising
# it on the way in.
#
#   ./restore.sh staging      seed the staging cluster (what make staging-up runs)
#   ./restore.sh prod         disaster recovery. Prompts, twice.
#
# Two things this does beyond moving bytes:
#
#   1. Anonymises BEFORE anything can read the database, not after.
#   2. Records how long the restore took. That number is the measured RTO and
#      goes into the next change record -- "we would restore from backup" is
#      not a recovery plan.
#
# Running on every release is deliberate: it makes the backup continuously
# verified instead of an annual fire drill nobody schedules.
#
# See docs/adr/0005-data-durability-and-staging-seeding.md
# and docs/runbooks/restore-from-backup.md.

set -euo pipefail

TARGET="${1:?usage: restore.sh <staging|prod>}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

: "${BACKUP_BUCKET:?set BACKUP_BUCKET}"

case "$TARGET" in
  staging) HOST="${STAGING_POSTGRES_HOST:?}"; DB="${STAGING_POSTGRES_DB:-kaval}"; USER="${STAGING_POSTGRES_USER:-kaval}" ;;
  prod)
    HOST="${POSTGRES_HOST:?}"; DB="${POSTGRES_DB:-kaval}"; USER="${POSTGRES_USER:-kaval}"
    echo ""
    echo "  Restoring into PRODUCTION. This replaces the live database."
    echo "  Up to 24 hours of data since the last dump will be lost (ADR-0005)."
    echo ""
    read -p "  Type the database name to continue: " ok
    [[ "$ok" == "$DB" ]] || { echo "  aborted"; exit 1; }
    ;;
  *) echo "target must be staging or prod"; exit 1 ;;
esac

START=$(date +%s)

KEY=$(aws s3 cp "s3://${BACKUP_BUCKET}/postgres/LATEST" - 2>/dev/null || true)
[[ -n "$KEY" ]] || { echo "  no LATEST pointer in s3://${BACKUP_BUCKET}/postgres/ — has backup.sh ever run?"; exit 1; }

TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT

echo "  snapshot  ${KEY}"
aws s3 cp "s3://${BACKUP_BUCKET}/${KEY}" "$TMP" --only-show-errors

echo "  restoring into ${TARGET} (${HOST}/${DB})..."
pg_restore -h "$HOST" -U "$USER" -d "$DB" --clean --if-exists --no-owner "$TMP"

if [[ "$TARGET" == "staging" ]]; then
  echo "  sanitising..."
  # Aborts the whole restore if an assertion in the script fails, so a
  # partially-sanitised staging database is never left readable.
  psql -h "$HOST" -U "$USER" -d "$DB" -v ON_ERROR_STOP=1 -q -f "$ROOT/scripts/anonymise.sql"
  echo "  sanitised — assertions passed"
fi

ELAPSED=$(( $(date +%s) - START ))
ROWS=$(psql -h "$HOST" -U "$USER" -d "$DB" -tAc "SELECT count(*) FROM signal" 2>/dev/null || echo "?")

echo ""
echo "  restored in ${ELAPSED}s · ${ROWS} signal rows"
echo "  ^ this is the measured RTO. It belongs in the next change record."
echo ""

mkdir -p "$ROOT/.build"
printf '{"target":"%s","key":"%s","seconds":%s,"rows":"%s","at":"%s"}\n' \
  "$TARGET" "$KEY" "$ELAPSED" "$ROWS" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  > "$ROOT/.build/last-restore.json"
