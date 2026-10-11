#!/usr/bin/env bash
# Game days on staging (KAV-79, ADR-0044). One script, three verbs:
#
#   chaos.sh run [oom]   turn the switch on, start the experiment's Job from its suspended
#                        CronJob, follow it, print its timeline, turn the switch off again.
#   chaos.sh stop        the stop button: switch off, Jobs deleted, the victim deleted.
#   chaos.sh propose     staging has no language model: file the diagnosis it would have written
#                        (chaos_propose.py). The post-mortem says the run used this.
#   chaos.sh approve ID  the human step: approve a proposed action through the gateway.
#   chaos.sh status      the switch, the Jobs, the victim.
#
# Staging only. It finds the node by its staging name tag and refuses to run if there is none,
# so it cannot reach prod; the chart renders no chaos object in prod anyway, and the runner
# refuses any environment but local/staging (kaval_executor.chaos).
#
# Nothing here fixes anything. Kaval does that, through its normal approval path; this script
# only causes the failure and watches. Read chaos/game-day-template.md before a run.
# ASSUME_YES=1 skips the confirmation prompt.

set -euo pipefail

export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
export AWS_PROFILE="${AWS_PROFILE:-kaval}"
export AWS_REGION="${AWS_REGION:-ap-south-1}"

PREFIX="kaval-staging"
NAMESPACE="kaval-staging"
DEMO_NAMESPACE="kaval-demo-staging"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ASSUME_YES="${ASSUME_YES:-0}"
WAIT_SECONDS="${WAIT_SECONDS:-960}"   # the Job's own hard limit is 900
KC="KUBECONFIG=/etc/rancher/k3s/k3s.yaml /usr/local/bin/kubectl"

confirm() {
  [ "$ASSUME_YES" = "1" ] && return 0
  read -r -p "$1 [y/N] " ok
  [ "$ok" = "y" ]
}

node_instance_id() {
  aws ec2 describe-instances \
    --filters "Name=tag:Name,Values=${PREFIX}" "Name=instance-state-name,Values=running" \
    --query "Reservations[0].Instances[0].InstanceId" --output text 2>/dev/null | grep -oE '^i-[0-9a-f]+$' || true
}

# One shell command on the node over Session Manager. The command must not contain double quotes
# unless they are written \" (they go inside a JSON string).
on_node() {
  local instance="$1" cmd="$2" params id status
  params="$(mktemp)"
  printf '{"commands":["%s"]}' "$cmd" > "$params"
  local uri="$params"
  command -v cygpath >/dev/null 2>&1 && uri="$(cygpath -m "$params")"
  id=$(aws ssm send-command --instance-ids "$instance" --document-name AWS-RunShellScript \
        --parameters "file://$uri" --query "Command.CommandId" --output text)
  rm -f "$params"
  for _ in $(seq 1 40); do
    sleep 3
    status=$(aws ssm get-command-invocation --command-id "$id" --instance-id "$instance" \
              --query "Status" --output text 2>/dev/null || echo "Pending")
    case "$status" in Success | Failed | Cancelled | TimedOut) break ;; esac
  done
  aws ssm get-command-invocation --command-id "$id" --instance-id "$instance" \
    --query "StandardOutputContent" --output text 2>/dev/null || true
}

need_node() {
  NODE="$(node_instance_id)"
  if [ -z "$NODE" ]; then
    echo "Staging is not running (no node tagged ${PREFIX}). Start it with: make staging-up" >&2
    exit 1
  fi
}

# The chart names things <release>-kaval-...; ask the cluster rather than guess.
find_name() {  # kind, name part
  on_node "$NODE" "$KC get $1 -n ${NAMESPACE} -o name | grep $2 | head -1 | cut -d/ -f2" | tr -d '\r\n'
}

set_switch() {  # true|false
  local cm
  cm="$(find_name configmap chaos-switch)"
  [ -n "$cm" ] || { echo "No chaos switch in ${NAMESPACE}: is chaos enabled in staging's values and deployed?" >&2; return 1; }
  on_node "$NODE" "$KC patch configmap $cm -n ${NAMESPACE} --type merge -p '{\\\"data\\\":{\\\"enabled\\\":\\\"$1\\\"}}'" >/dev/null
  echo "switch: $1"
}

cmd_status() {
  need_node
  local cm
  cm="$(find_name configmap chaos-switch)"
  if [ -z "$cm" ]; then echo "chaos is not deployed in staging"; return 0; fi
  echo "switch : $(on_node "$NODE" "$KC get configmap $cm -n ${NAMESPACE} -o jsonpath='{.data.enabled}'")"
  echo "jobs   :"
  on_node "$NODE" "$KC get jobs -n ${NAMESPACE} -l app.kubernetes.io/name=kaval-chaos-oom --no-headers 2>&1"
  echo "victim :"
  on_node "$NODE" "$KC get pods -n ${DEMO_NAMESPACE} -l app=chaos-oomer --no-headers 2>&1"
}

cmd_stop() {
  need_node
  set_switch false || true
  on_node "$NODE" "$KC delete jobs -n ${NAMESPACE} -l app.kubernetes.io/name=kaval-chaos-oom --ignore-not-found" || true
  on_node "$NODE" "$KC delete deployment chaos-oomer -n ${DEMO_NAMESPACE} --ignore-not-found" || true
  echo "stopped: switch off, Jobs and victim deleted."
}

# A whole script on the node, sent as base64 so no quoting survives to be mangled.
run_script_on_node() {  # script text on stdin
  local b64
  b64="$(base64 -w0 2>/dev/null || base64)"
  on_node "$NODE" "echo ${b64} | base64 -d | bash 2>&1"
}

cmd_propose() {
  need_node
  local py cj
  py="$(base64 -w0 "$HERE/chaos_propose.py" 2>/dev/null || base64 "$HERE/chaos_propose.py")"
  cj="$(find_name cronjob chaos-oom)"
  [ -n "$cj" ] || { echo "chaos is not deployed in staging" >&2; exit 1; }
  # A throwaway pod from the executor image, with the AGENT's database role, 256 MiB, deleted after.
  run_script_on_node <<SCRIPT
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
K=/usr/local/bin/kubectl
IMG=\$(\$K get cronjob ${cj} -n ${NAMESPACE} -o jsonpath='{.spec.jobTemplate.spec.template.spec.containers[0].image}')
AGENT_SECRET=\$(\$K get secret -n ${NAMESPACE} -o name | grep postgres-agent | head -1 | cut -d/ -f2)
echo ${py} | base64 -d | \$K run chaos-propose -n ${NAMESPACE} -i --rm --restart=Never --quiet --image="\$IMG" \
  --overrides='{"spec":{"imagePullSecrets":[{"name":"ecr-cred"}],"containers":[{"name":"chaos-propose","image":"'"\$IMG"'","stdin":true,"command":["python","-"],"envFrom":[{"secretRef":{"name":"'"\$AGENT_SECRET"'"}}],"env":[{"name":"CHAOS_TARGET_NAMESPACE","value":"${DEMO_NAMESPACE}"}],"resources":{"limits":{"memory":"256Mi"}}}]}}'
SCRIPT
}

cmd_approve() {
  local id="${1:-}"
  [ -n "$id" ] || { echo "usage: chaos.sh approve <action-id> (printed by 'make chaos-propose')" >&2; exit 2; }
  need_node
  confirm "Approve action ${id} (raise a memory limit on the experiment's victim)?" || { echo "aborted"; exit 1; }
  run_script_on_node <<SCRIPT
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
K=/usr/local/bin/kubectl
IP=\$(\$K get svc -n ${NAMESPACE} -o jsonpath='{.items[?(@.spec.ports[0].port==8000)].spec.clusterIP}')
curl -s -w ' HTTP %{http_code}' -X POST "http://\$IP:8000/v1/actions/${id}/decisions" -H 'Content-Type: application/json' \
  -d '{"verdict":"approved","actor":"${CHAOS_APPROVER:-roshan}","reason":"game day"}'
SCRIPT
}

cmd_run() {
  local experiment="${1:-oom}"
  [ "$experiment" = "oom" ] || { echo "unknown experiment: $experiment (have: oom)" >&2; exit 2; }
  need_node
  echo "Game day: ${experiment}, on staging only. The victim lives in ${DEMO_NAMESPACE}."
  echo "Kaval should notice, and you will be asked to approve the fix. Stop button: make chaos-stop"
  confirm "Start?" || { echo "aborted"; exit 1; }

  local cj job
  cj="$(find_name cronjob chaos-${experiment})"
  [ -n "$cj" ] || { echo "No chaos-${experiment} CronJob in ${NAMESPACE}: is chaos deployed to staging?" >&2; exit 1; }
  job="chaos-${experiment}-$(date +%H%M%S)"

  trap 'echo; echo "switching off..."; set_switch false || echo "!! could not turn the switch off: run make chaos-stop"' EXIT
  set_switch true
  echo "started: $(date -u +%H:%M:%SZ)"
  on_node "$NODE" "$KC create job $job --from=cronjob/$cj -n ${NAMESPACE}" >/dev/null

  local waited=0 state
  while [ "$waited" -lt "$WAIT_SECONDS" ]; do
    sleep 20
    waited=$((waited + 20))
    state="$(on_node "$NODE" "$KC get job $job -n ${NAMESPACE} -o jsonpath='{.status.succeeded}/{.status.failed}'")"
    echo "  ${waited}s  job(succeeded/failed)=${state:-?}   victim: $(on_node "$NODE" "$KC get pods -n ${DEMO_NAMESPACE} -l app=chaos-oomer --no-headers | head -1" | tr -s ' ')"
    case "$state" in 1/* ) break ;; */1 | */2 ) break ;; esac
  done
  echo "--- the runner's timeline ---"
  on_node "$NODE" "$KC logs job/$job -n ${NAMESPACE} 2>&1 | tail -5"
  echo "--- how long each stage took: make mttr ---"
}

# Sourced by a test, nothing runs; executed, it dispatches.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
case "${1:-}" in
  run)    cmd_run "${2:-oom}" ;;
  stop)   cmd_stop ;;
  status) cmd_status ;;
  propose) cmd_propose ;;
  approve) cmd_approve "${2:-}" ;;
  *) echo "usage: chaos.sh <run [oom]|stop|status|propose|approve ID>"; exit 2 ;;
esac
fi
