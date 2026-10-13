#!/usr/bin/env bash
# Restore drill: prove the newest production dump can really be restored, time it, write it down
# (KAV-82, ADR-0049).
#
#   make restore-drill      about a minute, costs nothing; run it once a month (the daily backup
#                           check complains after 35 days)
#
# What it does, in order:
#   1. reads the newest production dump from production's backup bucket (READ ONLY; nothing is
#      written to AWS and nothing billed is started);
#   2. starts a throwaway Postgres in Docker, the same image the real servers use;
#   3. restores the dump into it STRICTLY (any error stops it: a partly restored copy proves nothing);
#   4. counts the rows in the tables that matter and reads the schema version;
#   5. deletes the raw copy and the container, and hands every number to restore_drill_record.py,
#      which decides pass or fail and writes docs/drills/restore-<date>.md (a failed drill is written
#      as restore-failed-<date>.md, so it never counts as a drill having been done).
#
# This proves the dump. Replacing the database SERVER is a different, already measured step (Lab 27,
# and `make staging-up` repeats it on every release); the record adds that figure to the total.
#
# The raw dump holds production data, so it only ever lives in a temp folder on this machine and is
# deleted when the script ends, exactly as in seed-refresh.sh (ADR-0039).

set -euo pipefail

export MSYS_NO_PATHCONV=1 PYTHONUTF8=1 PYTHONIOENCODING=utf-8
export AWS_PROFILE="${AWS_PROFILE:-kaval}"
export AWS_REGION="${AWS_REGION:-ap-south-1}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IMAGE="${SEED_IMAGE:-pgvector/pgvector:pg16}"
NAME="kaval-drill-$$"
PYTHON="${PYTHON:-python}"
TABLES="signal incident incident_signal proposal action decision execution outcome notification alembic_version"

command -v docker > /dev/null || { echo "  docker is not installed"; exit 1; }
docker info > /dev/null 2>&1 || { echo "  Docker is not running. Start Docker Desktop, wait for it to say it is running, and try again."; exit 1; }

BUCKET="${PROD_BACKUP_BUCKET:-}"
if [[ -z "$BUCKET" ]]; then
  FOUND=$(aws s3api list-buckets --query "Buckets[?starts_with(Name, 'kaval-prod-db-backups-')].Name" --output text | tr -d '\r')
  [[ -n "$FOUND" && "$FOUND" != *$'\t'* && "$FOUND" != *' '* ]] \
    || { echo "  expected exactly one bucket named kaval-prod-db-backups-*, found: '${FOUND:-none}'. Set PROD_BACKUP_BUCKET."; exit 1; }
  BUCKET="$FOUND"
fi

KEY=$(aws s3 cp "s3://${BUCKET}/postgres/LATEST" - 2>/dev/null | tr -d '\r\n' || true)
[[ -n "$KEY" ]] || { echo "  no postgres/LATEST in the production bucket -- has production ever taken a dump? (ADR-0038)"; exit 1; }
STAMP=$(echo "$KEY" | sed -E 's/.*-([0-9]{8})T([0-9]{6})Z\.dump$/\1T\2/')
[[ "$STAMP" =~ ^[0-9]{8}T[0-9]{6}$ ]] || { echo "  cannot read a time out of the dump's name: $KEY"; exit 1; }
TAKEN="${STAMP:0:4}-${STAMP:4:2}-${STAMP:6:2}T${STAMP:9:2}:${STAMP:11:2}:${STAMP:13:2}Z"
echo "  newest dump      ${KEY}  (taken ${TAKEN})"

WORK=$(mktemp -d)
cleanup() {
  docker rm -f "$NAME" > /dev/null 2>&1 || true
  rm -rf "$WORK"   # the raw dump lives only here
}
trap cleanup EXIT

AWS_WORK="$WORK"
command -v cygpath > /dev/null 2>&1 && AWS_WORK="$(cygpath -m "$WORK")"

T0=$(date +%s.%N 2>/dev/null || date +%s)
aws s3 cp "s3://${BUCKET}/${KEY}" "$AWS_WORK/raw.dump" --only-show-errors
[[ -s "$WORK/raw.dump" ]] || { echo "  the download did not produce a file where expected -- stopping"; exit 1; }
T1=$(date +%s.%N 2>/dev/null || date +%s)
BYTES=$(wc -c < "$WORK/raw.dump" | tr -d ' ')
echo "  downloaded       $(du -h "$WORK/raw.dump" | cut -f1)"

echo "  starting a throwaway Postgres (${IMAGE})..."
docker run -d --rm --name "$NAME" -e POSTGRES_PASSWORD=drill -e POSTGRES_DB=kaval "$IMAGE" > /dev/null
for _ in $(seq 1 60); do
  [[ "$(docker logs "$NAME" 2>&1 | grep -c 'ready to accept connections')" -ge 2 ]] && break
  sleep 1
done
[[ "$(docker logs "$NAME" 2>&1 | grep -c 'ready to accept connections')" -ge 2 ]] || { echo "  the throwaway Postgres did not start"; exit 1; }

echo "  restoring strictly (any error stops it)..."
T2=$(date +%s.%N 2>/dev/null || date +%s)
RESTORE_OK=yes
docker exec -i "$NAME" pg_restore -U postgres -d kaval --no-owner --no-privileges --exit-on-error < "$WORK/raw.dump" || RESTORE_OK=no
T3=$(date +%s.%N 2>/dev/null || date +%s)

COUNTS=""
for t in $TABLES; do
  n=$(docker exec -i "$NAME" psql -U postgres -d kaval -tA -c "SELECT CASE WHEN to_regclass('public.${t}') IS NULL THEN -1 ELSE (xpath('/row/c/text()', query_to_xml('SELECT count(*) AS c FROM public.${t}', false, true, '')))[1]::text::int END" 2>/dev/null | tr -d '\r\n ' || true)
  # -1 or empty = the table is not there; leave it out so the verdict names it as missing
  [[ -n "$n" && "$n" != "-1" ]] && COUNTS="${COUNTS}${t}=${n},"
done
COUNTS="${COUNTS%,}"
ALEMBIC=$(docker exec -i "$NAME" psql -U postgres -d kaval -tA -c "SELECT version_num FROM alembic_version LIMIT 1" 2>/dev/null | tr -d '\r\n ' || true)

DOWNLOAD=$(awk -v a="$T0" -v b="$T1" 'BEGIN{printf "%.1f", b-a}')
RESTORE=$(awk -v a="$T2" -v b="$T3" 'BEGIN{printf "%.1f", b-a}')

# Relative paths from the repository root: on Windows, python is a native program and does not
# understand Git Bash's /c/... absolute paths (the same trap as the AWS CLI above).
cd "$ROOT"
set +e
"$PYTHON" scripts/ops/restore_drill_record.py \
  --key "$KEY" --taken "$TAKEN" --bytes "$BYTES" \
  --download-seconds "$DOWNLOAD" --restore-seconds "$RESTORE" --restore-ok "$RESTORE_OK" \
  --alembic "${ALEMBIC:-none}" --tables "${COUNTS:-none}" \
  --migrations migrations/versions --out-dir docs/drills
CODE=$?
set -e
echo ""
if [[ $CODE -eq 0 ]]; then
  echo "  The record is in docs/drills/. Commit it (docs: KAV-82 restore drill <date>); the daily check reads its date."
else
  echo "  The drill FAILED. Read the record in docs/drills/ (restore-failed-<date>.md) and docs/runbooks/restore-drill.md."
fi
exit $CODE
