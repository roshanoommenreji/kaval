#!/usr/bin/env bash
# Runs ON a database server (sent there by db-image-refresh.sh over SSM). Makes the server's
# Postgres unit run the image named in $PIN, pulling it first, and proves the database came back.
#
# Why this exists (KAV-169, ADR-0107): the database server's start-up script is read once, when the
# server is first built, and Terraform ignores later edits to it (a boot script must never replace a
# running database). So an image pin changed in git reaches a NEW server (staging is rebuilt every
# time) but never a server that already exists (production's). This is the explicit step that moves
# an existing server onto the pin. It never touches the data directory and never changes the
# PostgreSQL major version: it only swaps which build of the same image runs.
#
# Idempotent: when the unit already names the pin it does nothing and says so.
#   PIN=<image@digest> UNIT=/etc/systemd/system/kaval-postgres.service  db-image-refresh-remote.sh
#   db-image-refresh-remote.sh --rewrite-only <unit file> <pin>   # only edit the file (the tests use this)
set -euo pipefail

IMAGE_RE='pgvector/pgvector[^ ]*'

rewrite_unit() {   # rewrite_unit <unit file> <pin>: print "unchanged" or "changed <old>"
  local file="$1" pin="$2" current
  current="$(grep -o "$IMAGE_RE" "$file" | head -1)"
  if [ -z "$current" ]; then echo "no pgvector image line in $file" >&2; return 2; fi
  if [ "$current" = "$pin" ]; then echo "unchanged"; return 0; fi
  sed -i "s#${IMAGE_RE}#${pin}#" "$file"
  echo "changed $current"
}

if [ "${1:-}" = "--rewrite-only" ]; then
  rewrite_unit "$2" "$3"
  exit $?
fi

: "${PIN:?set PIN to the image and digest}"
UNIT="${UNIT:-/etc/systemd/system/kaval-postgres.service}"
case "$PIN" in pgvector/pgvector:*-pg16-*@sha256:*) ;; *) echo "refusing: $PIN is not a pgvector PostgreSQL 16 image pinned by digest" >&2; exit 2;; esac

docker pull "$PIN"                       # before the unit changes: a failed pull leaves the old one running
result="$(rewrite_unit "$UNIT" "$PIN")"
echo "unit: $result"
if [ "$result" = "unchanged" ]; then
  running="$(docker inspect --format '{{.Config.Image}}' postgres 2>/dev/null || true)"
  [ "$running" = "$PIN" ] && { echo "already running the pin"; exit 0; }
fi
systemctl daemon-reload
systemctl restart kaval-postgres         # ExecStop gives it 60 s for a clean checkpoint
for _ in $(seq 1 30); do
  docker exec postgres pg_isready -U kaval -d kaval >/dev/null 2>&1 && break
  sleep 2
done
docker exec postgres pg_isready -U kaval -d kaval
echo "running: $(docker inspect --format '{{.Config.Image}}' postgres)"
docker exec postgres psql -U kaval -d kaval -tAc "select 'extension '||extname||' '||extversion from pg_extension where extname='vector'; select 'server '||current_setting('server_version')"
