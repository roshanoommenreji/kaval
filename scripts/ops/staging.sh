#!/usr/bin/env bash
# The staging cluster's lifecycle (ADR-0004, ADR-0029). One script, three verbs:
#
#   staging.sh up       resume the database if it is parked, apply infra/envs/staging, wait
#                       until the services answer. STARTS BILLING (~$0.045/hr). On a fresh staging
#                       it also hands over the cleaned copy from `make seed-refresh` (ADR-0039).
#   staging.sh status   what is running, and the pods, read from the node over Session Manager.
#   staging.sh down     destroy everything, including the database's data volume.
#
# Staging parks itself after four idle hours (infra/modules/idle-stop): the node's Auto Scaling
# Group goes to zero and the database server stops. `up` resumes a parked staging; `down` is the
# deliberate teardown. Both are safe to re-run.
#
# ASSUME_YES=1 skips the confirmation prompts (the plan is still printed first).
# See docs/labs/lab-30-staging-up-down-and-idle-stop.md.

set -euo pipefail

# Windows: the AWS CLI's Python otherwise chokes on non-ASCII in command output.
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
export AWS_PROFILE="${AWS_PROFILE:-kaval}"
export AWS_REGION="${AWS_REGION:-ap-south-1}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TF="$ROOT/infra/envs/staging"
PREFIX="kaval-staging"
NAMESPACE="kaval-staging"
SEED_DIR="$ROOT/.build/seed"
ASSUME_YES="${ASSUME_YES:-0}"
WAIT_MINUTES="${WAIT_MINUTES:-15}"

confirm() {
  [ "$ASSUME_YES" = "1" ] && return 0
  read -r -p "$1 [y/N] " ok
  [ "$ok" = "y" ]
}

tf() { terraform -chdir="$TF" "$@"; }

# ── helpers ────────────────────────────────────────────────────────────────

node_instance_id() {
  aws ec2 describe-instances \
    --filters "Name=tag:Name,Values=${PREFIX}" "Name=instance-state-name,Values=running" \
    --query "Reservations[0].Instances[0].InstanceId" --output text 2>/dev/null | grep -oE '^i-[0-9a-f]+$' || true
}

database_state() {
  aws ec2 describe-instances \
    --filters "Name=tag:Name,Values=${PREFIX}-database" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
    --query "Reservations[0].Instances[0].State.Name" --output text 2>/dev/null | grep -v '^None$' || echo "none"
}

# Run one shell command on the node over Session Manager and print its output. Read-only use.
on_node() {
  local instance="$1" cmd="$2" params id status
  params="$(mktemp)"
  printf '{"commands":["%s"]}' "$cmd" > "$params"
  local uri="$params"
  command -v cygpath >/dev/null 2>&1 && uri="$(cygpath -m "$params")"
  id=$(aws ssm send-command --instance-ids "$instance" --document-name AWS-RunShellScript \
        --parameters "file://$uri" --query "Command.CommandId" --output text)
  rm -f "$params"
  for _ in $(seq 1 30); do
    sleep 3
    status=$(aws ssm get-command-invocation --command-id "$id" --instance-id "$instance" \
              --query "Status" --output text 2>/dev/null || echo "Pending")
    case "$status" in
      Success) break ;;
      Failed|Cancelled|TimedOut) break ;;
    esac
  done
  aws ssm get-command-invocation --command-id "$id" --instance-id "$instance" \
    --query "StandardOutputContent" --output text 2>/dev/null || true
}

# Hand the cleaned copy (made on this laptop by `make seed-refresh`, ADR-0039) to the node by
# putting it in staging's own bucket. The node, booting at about this moment, waits up to ten
# minutes for seed/LATEST (a cleaned copy) or seed/NONE (nothing to give) and then fills its EMPTY
# database from it before any service starts. Only the pointer is written last, so the node never
# sees a pointer to a file that is not there yet.
upload_seed() {
  local bucket key
  bucket="$(tf output -raw backup_bucket_name 2>/dev/null | tr -d '\r' || true)"
  if [ -z "$bucket" ]; then
    echo "Seed: could not read staging's bucket name - staging will start with an EMPTY database."
    return 0
  fi
  # Already handed over for this staging (a resumed one, or a re-run of `up`): leave it. The bucket
  # is destroyed with staging, so "something is in seed/" means "this staging was already given its
  # answer". A staging whose database already has tables ignores the seed anyway.
  if aws s3 ls "s3://${bucket}/seed/" > /dev/null 2>&1; then
    echo "Seed: already handed over to this staging - leaving it."
    return 0
  fi
  if [ -f "$SEED_DIR/clean.dump" ]; then
    key="seed/clean-$(date -u +%Y%m%dT%H%M%SZ).dump"
    aws s3 cp "$SEED_DIR/clean.dump" "s3://${bucket}/${key}" --only-show-errors
    printf '%s' "$key" | aws s3 cp - "s3://${bucket}/seed/LATEST" --only-show-errors
    echo "Seed: put the cleaned copy in staging's bucket ($(tr -d '\r\n' < "$SEED_DIR/clean.json"))."
  else
    printf 'none' | aws s3 cp - "s3://${bucket}/seed/NONE" --only-show-errors
    echo "Seed: there is no cleaned copy on this laptop (run 'make seed-refresh' first)."
    echo "      Staging will start with an EMPTY database, as it did before."
  fi
}

# What the node did about seeding, read from the node (its status file), so a staging that came
# up empty says so instead of looking like a seeded one.
seed_report() {
  local node="$1" report
  report="$(on_node "$node" "cat /var/lib/kaval/seed-status.json 2>/dev/null" | tr -d '\r\n')"
  if [ -z "$report" ]; then
    echo "Seed status: none recorded (this node was not set up to seed)."
  else
    echo "Seed status: $report"
    case "$report" in
      *'"status":"ok"'*) ;;
      *'"status":"skipped"'*) echo "             (skipped is normal for a resumed staging: its database already had tables.)" ;;
      *) echo "             !! Staging did NOT get the cleaned data. Why: /var/log/cloud-init-output.log on the node (Session Manager)." ;;
    esac
  fi
}

pods_ready() {
  # Ready = at least the four services listed, and every pod either Running n/n or Completed.
  awk 'NF { total++; split($2, r, "/"); if ($3 == "Completed" || ($3 == "Running" && r[1] == r[2])) ok++ }
       END { exit !(total >= 4 && ok == total) }'
}

# ── verbs ──────────────────────────────────────────────────────────────────

cmd_status() {
  echo "database : $(database_state)"
  local node
  node="$(node_instance_id)"
  if [ -z "$node" ]; then
    echo "node     : none running"
    return 0
  fi
  echo "node     : $node"
  on_node "$node" "KUBECONFIG=/etc/rancher/k3s/k3s.yaml /usr/local/bin/kubectl get pods -n ${NAMESPACE} --no-headers 2>&1 | head -20"
}

cmd_up() {
  echo "Staging starts billing at roughly \$0.045/hr (node, database server, two public IPv4)."
  echo "It parks itself after four idle hours; 'staging.sh down' destroys it."
  confirm "Continue?" || { echo "aborted"; exit 1; }

  tf init -input=false >/dev/null

  # A parked staging has a stopped database. Start it before the node so the services find it.
  if [ "$(database_state)" = "stopped" ]; then
    DB_NAME_PREFIX="$PREFIX" bash "$ROOT/scripts/ops/resume-database.sh"
  fi

  if [ "$ASSUME_YES" = "1" ]; then
    tf plan -input=false
    tf apply -input=false -auto-approve
  else
    tf apply
  fi

  upload_seed

  echo "Waiting up to ${WAIT_MINUTES} min for the node to come up and Flux to reconcile..."
  local deadline=$(( $(date +%s) + WAIT_MINUTES * 60 )) node pods
  while [ "$(date +%s)" -lt "$deadline" ]; do
    node="$(node_instance_id)"
    if [ -n "$node" ]; then
      pods="$(on_node "$node" "KUBECONFIG=/etc/rancher/k3s/k3s.yaml /usr/local/bin/kubectl get pods -n ${NAMESPACE} --no-headers 2>/dev/null" || true)"
      if [ -n "$pods" ] && printf '%s\n' "$pods" | pods_ready; then
        echo ""
        printf '%s\n' "$pods"
        echo ""
        seed_report "$node"
        echo ""
        echo "Staging is up. Look at it with: make staging-status"
        return 0
      fi
    fi
    sleep 20
  done
  echo "Not ready after ${WAIT_MINUTES} minutes. Not necessarily broken (Flux can be slow): make staging-status"
  exit 1
}

cmd_down() {
  if [ -z "$(tf state list 2>/dev/null)" ]; then
    echo "Nothing in staging's Terraform state - nothing to destroy."
  else
    echo "This destroys staging: the node, the database server and its data volume, the VPC."
    echo "Snapshots named ${PREFIX}-database* are deleted too."
    confirm "Destroy staging?" || { echo "aborted"; exit 1; }

    # The data volume has prevent_destroy (its address is wired into restore-snapshot.sh), so
    # it is released from state and deleted by hand below. Stop the database first: detaching
    # a mounted volume from a running instance is the failure to avoid (Lab 29).
    local db_id
    db_id="$(tf output -raw database_instance_id 2>/dev/null | grep -oE '^i-[0-9a-f]+$' || true)"
    if [ -n "$db_id" ] && [ "$(database_state)" != "stopped" ]; then
      echo "Stopping the database server..."
      aws ec2 stop-instances --instance-ids "$db_id" >/dev/null
      aws ec2 wait instance-stopped --instance-ids "$db_id"
    fi

    if tf state list | grep -qx 'module.database.aws_ebs_volume.data'; then
      tf state rm module.database.aws_ebs_volume.data
    fi

    if [ "$ASSUME_YES" = "1" ]; then
      tf plan -destroy -input=false -out=destroy.tfplan
      tf apply -input=false destroy.tfplan
      rm -f "$TF/destroy.tfplan"
    else
      tf destroy
    fi
  fi

  # Looked up by tag, not by state: this must work when a previous run died after the state rm.
  local vol snap
  for vol in $(aws ec2 describe-volumes \
      --filters "Name=tag:Name,Values=${PREFIX}-database-data" "Name=tag:Env,Values=staging" "Name=status,Values=available" \
      --query "Volumes[].VolumeId" --output text); do
    echo "Deleting the detached data volume ${vol}"
    aws ec2 delete-volume --volume-id "$vol"
  done
  for snap in $(aws ec2 describe-snapshots --owner-ids self \
      --filters "Name=tag:Name,Values=${PREFIX}-database*" \
      --query "Snapshots[].SnapshotId" --output text); do
    echo "Deleting snapshot ${snap}"
    aws ec2 delete-snapshot --snapshot-id "$snap"
  done

  local left
  left=$(aws ec2 describe-instances --filters "Name=tag:Env,Values=staging" \
    "Name=instance-state-name,Values=pending,running,stopping,stopped" \
    --query "length(Reservations[].Instances[])" --output text)
  left="$left instances, $(aws ec2 describe-volumes --filters "Name=tag:Env,Values=staging" \
    --query "length(Volumes)" --output text) volumes, $(aws ec2 describe-vpcs --filters "Name=tag:Env,Values=staging" \
    --query "length(Vpcs)" --output text) VPCs"
  echo "Left tagged Env=staging: ${left}"
}

case "${1:-}" in
  up)     cmd_up ;;
  status) cmd_status ;;
  down)   cmd_down ;;
  *) echo "usage: staging.sh <up|status|down>"; exit 2 ;;
esac
