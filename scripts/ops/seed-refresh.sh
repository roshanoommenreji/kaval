#!/usr/bin/env bash
# Make a CLEANED copy of production's latest database dump, on this machine, for staging to be
# filled from (KAV-73, ADR-0039).
#
#   make seed-refresh      run it whenever you want staging to get fresher data
#
# What it does, in order:
#   1. reads the newest production dump from production's backup bucket (read-only);
#   2. starts a throwaway Postgres in Docker and restores the dump into it;
#   3. runs scripts/ops/anonymise.sql there (the same scrub restore.sh runs, ADR-0037), which
#      either finishes AND proves a second scrub changes nothing, or fails and stops everything;
#   4. looks at the result with a SECOND, independent check (plain patterns, not the scrub's own
#      code) for anything that still looks like a key, account ARN, token or e-mail address;
#   5. dumps the cleaned database to .build/seed/clean.dump, and throws the raw copy away.
#
# `make staging-up` then uploads .build/seed/clean.dump to staging's own bucket, and the staging
# node fills its database from it. Staging never reads production's bucket and never sees the raw
# dump: the only thing that crosses is a file that has already been scrubbed (ADR-0039).
#
# Needs Docker running, and AWS access that can read production's backup bucket.
# Nothing is written to AWS. Nothing here starts a billed resource.

set -euo pipefail

# Windows / Git Bash: stop the shell rewriting "/tmp/..." style arguments handed to docker, and
# stop the AWS CLI's Python choking on non-ASCII in command output.
export MSYS_NO_PATHCONV=1 PYTHONUTF8=1 PYTHONIOENCODING=utf-8
export AWS_PROFILE="${AWS_PROFILE:-kaval}"
export AWS_REGION="${AWS_REGION:-ap-south-1}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="$ROOT/.build/seed"
IMAGE="${SEED_IMAGE:-pgvector/pgvector:pg16}"   # same major version and pgvector as the real servers
NAME="kaval-seed-$$"

command -v docker > /dev/null || { echo "  docker is not installed"; exit 1; }
docker info > /dev/null 2>&1 || { echo "  Docker is not running. Start Docker Desktop, wait for it to say it is running, and try again."; exit 1; }

# Production's backup bucket has a random suffix, so find it by its fixed prefix. Exactly one
# must match: if there are two, guess nothing and ask.
BUCKET="${PROD_BACKUP_BUCKET:-}"
if [[ -z "$BUCKET" ]]; then
  FOUND=$(aws s3api list-buckets --query "Buckets[?starts_with(Name, 'kaval-prod-db-backups-')].Name" --output text | tr -d '\r')
  [[ -n "$FOUND" && "$FOUND" != *$'\t'* && "$FOUND" != *' '* ]] \
    || { echo "  expected exactly one bucket named kaval-prod-db-backups-*, found: '${FOUND:-none}'. Set PROD_BACKUP_BUCKET."; exit 1; }
  BUCKET="$FOUND"
fi

KEY=$(aws s3 cp "s3://${BUCKET}/postgres/LATEST" - 2>/dev/null | tr -d '\r\n' || true)
[[ -n "$KEY" ]] || { echo "  no postgres/LATEST in the production bucket -- has production ever taken a dump? (ADR-0038)"; exit 1; }
echo "  production dump  ${KEY}"

WORK=$(mktemp -d)
cleanup() {
  docker rm -f "$NAME" > /dev/null 2>&1 || true
  rm -rf "$WORK"   # the raw dump lives only here
}
trap cleanup EXIT

aws s3 cp "s3://${BUCKET}/${KEY}" "$WORK/raw.dump" --only-show-errors
echo "  downloaded       $(du -h "$WORK/raw.dump" | cut -f1) (raw: deleted when this script ends)"

echo "  starting a throwaway Postgres (${IMAGE})..."
docker run -d --rm --name "$NAME" -e POSTGRES_PASSWORD=seed -e POSTGRES_DB=kaval "$IMAGE" > /dev/null
# The image starts a temporary server to create the database, stops it, and starts the real one:
# "ready" is logged twice, and only the second means it is the real one.
for _ in $(seq 1 60); do
  [[ "$(docker logs "$NAME" 2>&1 | grep -c 'ready to accept connections')" -ge 2 ]] && break
  sleep 1
done
[[ "$(docker logs "$NAME" 2>&1 | grep -c 'ready to accept connections')" -ge 2 ]] || { echo "  the throwaway Postgres did not start"; exit 1; }

psql_in()  { docker exec -i "$NAME" psql -U postgres -d kaval -v ON_ERROR_STOP=1 -q "$@"; }

echo "  restoring the raw copy into it..."
# Strict (no tolerated errors): a partly restored copy must not be cleaned and carried.
# --no-owner/--no-privileges: the dump's owners and grants name production's roles, which do not exist here.
docker exec -i "$NAME" pg_restore -U postgres -d kaval --no-owner --no-privileges --exit-on-error < "$WORK/raw.dump"

echo "  scrubbing (one transaction; ends with a check that a second scrub changes nothing)..."
psql_in --single-transaction -f - < "$ROOT/scripts/ops/anonymise.sql"

echo "  dumping the cleaned copy..."
docker exec "$NAME" pg_dump -U postgres -d kaval -Fc --no-owner --no-privileges > "$WORK/clean.dump"
docker exec -i "$NAME" pg_restore --list < "$WORK/clean.dump" > /dev/null \
  || { echo "  the cleaned dump cannot be read back -- not keeping it"; exit 1; }

# A second opinion that shares no code with the scrub: read the cleaned dump as plain text and
# look for the shapes of things that must never reach staging. (12-digit numbers are NOT checked:
# the scrub leaves JSON numbers alone on purpose, and a byte count looks the same as an account id.)
# user@example.com is what the scrub itself writes in place of an address, so it is allowed.
# Only line numbers are printed, never the matched text.
docker exec -i "$NAME" pg_restore -f - < "$WORK/clean.dump" > "$WORK/clean.sql"
LEFT=$(grep -n -o -E \
  'AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|arn:aws[a-z-]*:[a-z0-9-]*:[a-z0-9-]*:[0-9]{12}:[^[:space:]"]*|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|xox[abpr]-[A-Za-z0-9-]{10,}|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' \
  "$WORK/clean.sql" | grep -v -E ':user@example\.com$' | cut -d: -f1 | sort -un | head -5 || true)
if [[ -n "$LEFT" ]]; then
  echo "  !! the cleaned copy STILL contains something that looks like a secret or an address (dump line numbers: $(echo $LEFT | tr '\n' ' '))."
  echo "  !! Not keeping it. Fix scripts/ops/anonymise.sql, add a test, and run this again."
  exit 1
fi

ROWS=$(psql_in -tA -c "SELECT count(*) FROM signal")
mkdir -p "$OUT"
mv "$WORK/clean.dump" "$OUT/clean.dump"
printf '{"source":"%s","cleaned_at":"%s","bytes":%s,"signal_rows":"%s"}\n' \
  "$KEY" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(wc -c < "$OUT/clean.dump" | tr -d ' ')" "$ROWS" > "$OUT/clean.json"

echo ""
echo "  cleaned copy ready: .build/seed/clean.dump ($(du -h "$OUT/clean.dump" | cut -f1), ${ROWS} signal rows)"
echo "  Next 'make staging-up' puts it in staging's bucket and staging fills its database from it."
echo "  Run this again whenever production has a newer dump and you want staging to have it."
