#!/usr/bin/env bash
# Take a fresh database dump on production's app server, just before it is parked (KAV-134).
#
# Why: the nightly dump (19:30 UTC, ADR-0038) is taken only on nights the app server happens to be
# running. Production is parked most of the time, so on 2026-11-08 the newest dump was 12 days old
# and the monthly restore drill, although the restore itself was clean, had to be recorded as
# failed (ADR-0049, "the dump has a hole the snapshot does not"). Parking now means: dump first.
#
# How: the app server already has the whole dump (kaval-db-backup.service: its own instance role,
# a Postgres 16 client, the same backup.sh the timer runs, which lists the archive before it
# uploads it). This script only asks that unit to run once, over Session Manager, waits, and reads
# the unit's own log (last five minutes only, so last night's "done." cannot be mistaken for this
# one's; `set -e` makes a unit that failed to start fail the command) to see it finish. No new permission, no new key, no new copy of the logic.
#
# Order matters: this must run BEFORE the app server group is scaled to 0 (the server is the thing
# that holds the tools) and BEFORE pause-database.sh (the database must be running to be dumped).
# `make down` does it in that order.
#
# When the dump fails, the park stops with exit 1 and nothing has been scaled down: a park that
# silently skips the dump is the hole this story closes. SKIP_DUMP=1 is the deliberate override
# (e.g. the database itself is broken and parking is the point); it says so loudly.
#
# Sourceable: nothing runs on `source`, so dump_succeeded can be tested without AWS.

set -euo pipefail

# Did the unit's log show a finished upload? "done." is the last line backup.sh prints after the
# upload and the move of the LATEST pointer; "NOT uploading" is its refusal of an unreadable file.
dump_succeeded() {
  local log="$1"
  case "$log" in *"NOT uploading"*) return 1 ;; esac
  case "$log" in *"  done."*) return 0 ;; esac
  return 1
}

# The bucket's name is never written into git, and a log pasted into a record should not carry it
# either: show the key and size, hide the bucket.
hide_bucket() { sed -E 's#s3://[^/ ]+/#s3://<bucket>/#g'; }

main() {
  export AWS_PROFILE="${AWS_PROFILE:-kaval}"
  export AWS_REGION="${AWS_REGION:-ap-south-1}"
  # backup.sh prints an arrow and an em dash. The AWS CLI on Windows cannot write them with its
  # default text encoding: the log then reads back EMPTY and a good dump is refused (found
  # parking production on 2026-11-13, KAV-148). UTF-8 on both ends fixes it.
  export PYTHONUTF8=1
  export PYTHONIOENCODING=utf-8
  local node_name="${NODE_NAME:-kaval-prod}" instance id status log params uri

  if [ "${SKIP_DUMP:-0}" = "1" ]; then
    echo "SKIP_DUMP=1: parking WITHOUT a fresh database dump. The newest dump may be days old, and a"
    echo "restore drill run on it will be recorded as failed. The data-volume snapshot is still taken."
    return 0
  fi

  instance=$(aws ec2 describe-instances \
    --filters "Name=tag:Name,Values=${node_name}" "Name=instance-state-name,Values=running" \
    --query "Reservations[0].Instances[0].InstanceId" --output text 2>/dev/null \
    | grep -oE '^i-[0-9a-f]+$' || true)
  if [ -z "$instance" ]; then
    echo "No running app server named ${node_name}: it is already parked, so there is nothing to dump from."
    return 0
  fi

  echo "Taking a fresh database dump on ${instance} before parking..."
  # A file, not shorthand: the journalctl line holds quotes, and the shorthand parser mangles them.
  params="$(mktemp)"
  cat > "$params" <<'JSON'
{"commands":["set -e","systemctl start kaval-db-backup.service","journalctl -u kaval-db-backup.service --since \"5 min ago\" --no-pager -o cat"]}
JSON
  uri="$params"
  command -v cygpath >/dev/null 2>&1 && uri="$(cygpath -m "$params")"
  id=$(aws ssm send-command --instance-ids "$instance" --document-name AWS-RunShellScript \
        --parameters "file://$uri" --query "Command.CommandId" --output text)
  rm -f "$params"

  status="Pending"
  for _ in $(seq 1 80); do
    sleep 3
    status=$(aws ssm get-command-invocation --command-id "$id" --instance-id "$instance" \
              --query "Status" --output text 2>/dev/null || echo "Pending")
    case "$status" in Success|Failed|Cancelled|TimedOut) break ;; esac
  done

  log=$(aws ssm get-command-invocation --command-id "$id" --instance-id "$instance" \
          --query "StandardOutputContent" --output text 2>/dev/null || true)
  printf '%s\n' "$log" | hide_bucket

  if [ "$status" = "Success" ] && dump_succeeded "$log"; then
    echo "Dump taken and uploaded. Safe to park."
    return 0
  fi

  echo "The dump did NOT finish (Session Manager status: ${status}). Nothing has been scaled down." >&2
  aws ssm get-command-invocation --command-id "$id" --instance-id "$instance" \
    --query "StandardErrorContent" --output text 2>/dev/null | hide_bucket >&2 || true
  echo "Fix it, or park anyway on purpose with: SKIP_DUMP=1 make down" >&2
  return 1
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
