#!/usr/bin/env bash
# Restore the latest production snapshot into a target database, sanitising
# it on the way in.
#
#   ./restore.sh staging      seed the staging cluster (what make staging-up runs)
#   ./restore.sh prod         disaster recovery. Prompts, twice.
#
# Three things this does beyond moving bytes:
#
#   1. Anonymises BEFORE anything can read the database, not after. Run it while no service
#      is connected to the target (ADR-0037): pg_restore has put the unscrubbed copy there
#      until the scrub finishes.
#   2. Fails closed. The scrub runs as one transaction; if its final check fails, the
#      restored tables are dropped, so a copy that could not be proven clean is never left
#      for a service to read. The exit code is non-zero and last-restore.json says "failed".
#   3. Records how long the restore took. That number is the measured RTO and
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
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

: "${BACKUP_BUCKET:?set BACKUP_BUCKET}"

case "$TARGET" in
  staging)
    HOST="${STAGING_POSTGRES_HOST:?}"; DB="${STAGING_POSTGRES_DB:-kaval}"; USER="${STAGING_POSTGRES_USER:-kaval}"
    PASSWORD="${STAGING_POSTGRES_PASSWORD:?}"
    # `--clean` below drops tables. Staging mode must never be pointed at the production
    # server by a mistyped variable, so refuse when the two are the same host.
    if [[ -n "${POSTGRES_HOST:-}" && "$HOST" == "$POSTGRES_HOST" ]]; then
      echo "  refusing: STAGING_POSTGRES_HOST is the same host as POSTGRES_HOST (production)"
      exit 1
    fi
    ;;
  prod)
    HOST="${POSTGRES_HOST:?}"; DB="${POSTGRES_DB:-kaval}"; USER="${POSTGRES_USER:-kaval}"
    PASSWORD="${POSTGRES_PASSWORD:?}"
    echo ""
    echo "  Restoring into PRODUCTION. This replaces the live database."
    echo "  Up to 24 hours of data since the last dump will be lost (ADR-0005)."
    echo ""
    read -p "  Type the database name to continue: " ok
    [[ "$ok" == "$DB" ]] || { echo "  aborted"; exit 1; }
    ;;
  *) echo "target must be staging or prod"; exit 1 ;;
esac

# pg_restore/psql have no password flag -- they read PGPASSWORD (or ~/.pgpass).
export PGPASSWORD="$PASSWORD"

START=$(date +%s)

# Which folder of the bucket to read. The default is the nightly dump (backup.sh writes
# postgres/); staging's seed is the cleaned copy a person carried over (seed/, ADR-0039).
FOLDER="${RESTORE_PREFIX:-postgres}"

KEY=$(aws s3 cp "s3://${BACKUP_BUCKET}/${FOLDER}/LATEST" - 2>/dev/null || true)
[[ -n "$KEY" ]] || { echo "  no LATEST pointer in s3://${BACKUP_BUCKET}/${FOLDER}/ — has a dump ever been put there?"; exit 1; }

TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT

echo "  snapshot  ${KEY}"
aws s3 cp "s3://${BACKUP_BUCKET}/${KEY}" "$TMP" --only-show-errors

echo "  restoring into ${TARGET} (${HOST}/${DB})..."
# Staging: leave the dump's GRANTs out. They name production's service roles, which do not
# exist on a fresh staging server yet, and every one would be an error. db-roles.sql is the one
# place that hands permissions out, after the restore and the schema upgrade (ADR-0039).
# Prod (disaster recovery) keeps them: the roles are still there, and so are the grants.
PRIVS=()
[[ "$TARGET" == "staging" ]] && PRIVS=(--no-privileges)
pg_restore -h "$HOST" -U "$USER" -d "$DB" --clean --if-exists --no-owner ${PRIVS[@]+"${PRIVS[@]}"} "$TMP"

record() {  # record <status>: one line for the change record, written on success and on failure
  mkdir -p "$ROOT/.build"
  printf '{"target":"%s","key":"%s","status":"%s","seconds":%s,"rows":"%s","at":"%s"}\n' \
    "$TARGET" "$KEY" "$1" "$(( $(date +%s) - START ))" "${ROWS:-?}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    > "$ROOT/.build/last-restore.json"
}

# Drops every table in the public schema: the "fail closed" step below.
DROP_ALL="DO \$\$ DECLARE t text; BEGIN
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
    EXECUTE format('DROP TABLE IF EXISTS public.%I CASCADE', t);
  END LOOP;
END \$\$;"

if [[ "$TARGET" == "staging" ]]; then
  echo "  sanitising..."
  # --single-transaction: the whole file commits or none of it does. If the final check in
  # the script fails, nothing is kept, and the restored (unscrubbed) tables are dropped
  # rather than left readable.
  if ! psql -h "$HOST" -U "$USER" -d "$DB" -v ON_ERROR_STOP=1 -q --single-transaction \
        -f "$ROOT/scripts/ops/anonymise.sql"; then
    echo "  sanitising FAILED - dropping the restored tables so nothing unscrubbed stays readable"
    psql -h "$HOST" -U "$USER" -d "$DB" -v ON_ERROR_STOP=1 -q -c "$DROP_ALL" \
      || echo "  !! could not drop them either: treat this database as unclean and destroy it"
    ROWS="?"; record failed
    exit 1
  fi
  echo "  sanitised - the final check passed"
fi

ELAPSED=$(( $(date +%s) - START ))
ROWS=$(psql -h "$HOST" -U "$USER" -d "$DB" -tAc "SELECT count(*) FROM signal" 2>/dev/null || echo "?")

echo ""
echo "  restored in ${ELAPSED}s · ${ROWS} signal rows"
echo "  ^ this is the measured RTO. It belongs in the next change record."
echo ""

record ok
