#!/usr/bin/env bash
# Store the outside "still alive" ping address (KAV-83, ADR-0052). Run by YOU, on your own machine.
#
#   scripts/ops/set-heartbeat.sh staging    # staging's own check: AWS only
#   scripts/ops/set-heartbeat.sh prod       # production's check: AWS, and GitHub for the parked cover
#
# The address is a secret (anyone who has it can keep a dead system looking alive), so it is typed
# or pasted into a hidden prompt here and goes nowhere else: it is never an argument (so it is not in
# the process list), never echoed, never written anywhere but a temporary file that only you can read
# and that is deleted when this ends. Never paste it to an assistant or into a chat.
#
# What it sets:
#   AWS  Parameter Store  /kaval/kaval-<env>/heartbeat/url   (SecureString; the node reads it at boot)
#   GitHub Actions secret PROD_HEARTBEAT_URL                 (prod only; the parked-cover job reads it)
#
# After a CHANGE of address on a running server, also run the node's start-up refresh and restart the
# poller (docs/runbooks/still-alive-alarm.md, "Changing the address"); a new server picks it up alone.
set -euo pipefail

env="${1:-}"
case "$env" in staging|prod) ;; *) echo "usage: $0 staging|prod" >&2; exit 2 ;; esac
: "${AWS_PROFILE:?set AWS_PROFILE (the project uses AWS_PROFILE=kaval)}"

read -r -s -p "Paste the ${env} ping address (it will not show), then Enter: " url
echo
case "$url" in
  https://*) ;;
  *) echo "That does not start with https:// : nothing was stored." >&2; exit 1 ;;
esac
case "$url" in *[[:space:]]*) echo "It contains a space: nothing was stored." >&2; exit 1 ;; esac

umask 077
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
printf '%s' "$url" > "$tmp"

# A file, not an argument: the AWS CLI would FETCH a value that starts with https://, and an argument
# shows in the process list. (On Windows the AWS CLI needs the Windows spelling of the path.)
path="$tmp"
if command -v cygpath >/dev/null 2>&1; then path="$(cygpath -m "$tmp")"; fi
aws ssm put-parameter --region ap-south-1 --name "/kaval/kaval-${env}/heartbeat/url" \
  --type SecureString --overwrite --value "file://${path}" >/dev/null
echo "Stored in AWS Parameter Store: /kaval/kaval-${env}/heartbeat/url"

if [ "$env" = prod ]; then
  gh secret set PROD_HEARTBEAT_URL < "$tmp"
  echo "Stored as the GitHub Actions secret PROD_HEARTBEAT_URL"
fi
unset url
echo "Done. The address was not shown or saved anywhere else."
