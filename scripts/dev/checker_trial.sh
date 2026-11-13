#!/usr/bin/env bash
# Laptop trial of the cluster's image check (KAV-140, ADR-0093, Lab 95). $0 on AWS: it only READS
# signatures and tags in ECR with your own login. Everything runs in a throwaway k3d cluster that
# is deleted at the end.
#
#   SIGNED_TAG=sha-152b59e scripts/dev/checker_trial.sh
#
# SIGNED_TAG   a release that was signed (built by release.yml after 2026-11-13). Required.
# UNSIGNED_TAG a release built before signing, still on the frozen list. Default: sha-791c44d.
# AWS_PROFILE  default kaval.   AWS_REGION  default ap-south-1.   KEEP=1  leave the cluster up.
#
# It installs Kyverno exactly as Flux does (the chart version, digests and settings are read from
# deploy/gitops/staging/checker-helmrelease.yaml), applies the REAL policy file with the registry
# filled in, and asks the API server five questions with a server-side dry run (nothing is created,
# no image is pulled). It exits 0 only if every answer is the expected one.
#
# The registry address holds the AWS account number, so it is worked out here from your login,
# never written down, and every line printed goes through scrub().
set -euo pipefail
cd "$(dirname "$0")/../.."

: "${SIGNED_TAG:?set SIGNED_TAG to a signed release tag, e.g. sha-152b59e}"
UNSIGNED_TAG="${UNSIGNED_TAG:-sha-791c44d}"
export AWS_PROFILE="${AWS_PROFILE:-kaval}" AWS_REGION="${AWS_REGION:-ap-south-1}"
CLUSTER=kav140
CTX="k3d-$CLUSTER"
POLICY=deploy/image-policy/image-validating-policy.yaml
RELEASE=deploy/gitops/staging/checker-helmrelease.yaml
WORK="$(mktemp -d)"

scrub() { sed -E 's/[0-9]{12}/<account>/g'; }
k() { kubectl --context "$CTX" "$@"; }

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REG="${ACCOUNT}.dkr.ecr.${AWS_REGION}.amazonaws.com"
unset ACCOUNT

cleanup() {
  # KEEP=1 leaves the throwaway cluster up so you can poke at it; delete it yourself afterwards.
  [ "${KEEP:-}" = 1 ] || k3d cluster delete "$CLUSTER" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

echo "== cluster (k3s 1.32: Docker Desktop's engine is too old for the newest kubelet)"
k3d cluster delete "$CLUSTER" >/dev/null 2>&1 || true
k3d cluster create "$CLUSTER" --image rancher/k3s:v1.32.2-k3s1 --servers 1 --agents 0 \
  --k3s-arg "--disable=traefik@server:0" --wait --timeout 240s >/dev/null 2>&1
# Docker Desktop on Windows publishes the API port on localhost but k3d writes a name that does not
# always resolve; point the context at localhost.
PORT="$(docker port "k3d-$CLUSTER-serverlb" 6443/tcp | head -1 | sed 's/.*://')"
kubectl config set-cluster "$CTX" --server="https://127.0.0.1:${PORT}" >/dev/null
k get nodes | tail -1

echo "== Kyverno, as Flux installs it"
python - "$RELEASE" "$WORK" <<'PY'
import sys, yaml
rel = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))["spec"]
yaml.safe_dump(rel["values"], open(sys.argv[2] + "/values.yaml", "w", encoding="utf-8"))
open(sys.argv[2] + "/version", "w").write(rel["chart"]["spec"]["version"])
PY
helm repo add kyverno https://kyverno.github.io/kyverno/ >/dev/null 2>&1 || true
helm repo update kyverno >/dev/null 2>&1 || true
helm install kyverno kyverno/kyverno --version "$(cat "$WORK/version")" -n kyverno --create-namespace \
  --kube-context "$CTX" -f "$WORK/values.yaml" --wait --timeout 300s 2>&1 | grep -E "^STATUS" || true

echo "== the registry login the node would write (token read once, never printed)"
k create namespace kaval-staging >/dev/null
k create namespace elsewhere >/dev/null
mkdir -p "$WORK/login"
PW="$(aws ecr get-login-password)"
AUTH="$(printf '%s' "AWS:$PW" | base64 | tr -d '\n')"
printf '{"auths":{"%s":{"username":"AWS","password":"%s","auth":"%s"}}}' "$REG" "$PW" "$AUTH" \
  > "$WORK/login/.dockerconfigjson"
unset PW AUTH
for ns in kyverno kaval-staging; do
  k -n "$ns" create secret generic ecr-cred --type=kubernetes.io/dockerconfigjson \
    --from-file=.dockerconfigjson="$WORK/login/.dockerconfigjson" 2>&1 | scrub
done
rm -rf "$WORK/login"

FROZEN_TAG="$(grep -oE "'sha-[0-9a-f]+'" "$POLICY" | tail -1 | tr -d "'")"
apply_policy() { # $1 = sed script applied on top of the repo file, for the trial only
  sed -e "s#\${registry}#$REG#g" -e 's#\${kaval_namespace}#kaval-staging#g' -e "$1" "$POLICY" \
    | k apply -f - 2>&1 | scrub
  sleep 8
}
pod() { # name namespace tag [init]
  local where="containers"
  [ "${4:-}" = init ] && where="init"
  if [ "$where" = init ]; then
    printf 'apiVersion: v1\nkind: Pod\nmetadata: {name: %s, namespace: %s}\nspec:\n  imagePullSecrets: [{name: ecr-cred}]\n  initContainers: [{name: i, image: "%s/kaval/gateway:%s"}]\n  containers: [{name: c, image: "nginx:1.27"}]\n' "$1" "$2" "$REG" "$3"
  else
    printf 'apiVersion: v1\nkind: Pod\nmetadata: {name: %s, namespace: %s}\nspec:\n  imagePullSecrets: [{name: ecr-cred}]\n  containers: [{name: c, image: "%s/kaval/gateway:%s"}]\n' "$1" "$2" "$REG" "$3"
  fi | k apply --dry-run=server -f - 2>&1 | scrub
}

FAILS=0
answer() { # what the API server said, in one word
  if grep -q "failed calling webhook" <<<"$1"; then echo unreachable
  elif grep -q "denied the request" <<<"$1"; then echo refused
  elif grep -qiE "^pod/.* (created|configured)" <<<"$1"; then echo allowed
  else echo "other"; fi
}
expect() { # label expected(allowed|refused|unreachable) output
  local got; got="$(answer "$3")"
  if [ "$got" = "$2" ]; then echo "  PASS  $1 -> $got"; else echo "  FAIL  $1 -> $got (wanted $2)"; echo "$3" | cut -c1-400 | sed 's/^/        /'; FAILS=$((FAILS + 1)); fi
}

echo "== the real policy, exactly as the file says (Deny, Fail)"
# The unsigned tag is taken off the frozen list for the trial, to play "an unsigned image that is NOT frozen".
apply_policy "/'${UNSIGNED_TAG}',/d"

expect "signed release in a Kaval namespace"              allowed "$(pod t1 kaval-staging "$SIGNED_TAG")"
expect "frozen old release ($FROZEN_TAG, unsigned)"       allowed "$(pod t2 kaval-staging "$FROZEN_TAG")"
expect "unsigned release NOT on the frozen list"          refused "$(pod t3 kaval-staging "$UNSIGNED_TAG")"
expect "the same unsigned image in an init container"     refused "$(pod t4 kaval-staging "$UNSIGNED_TAG" init)"
expect "the same unsigned image in another namespace"     allowed "$(pod t5 elsewhere "$UNSIGNED_TAG")"

echo "== control: the wrong signer must refuse even the signed release"
apply_policy "/'${UNSIGNED_TAG}',/d; s#release.yml@#ci.yml@#"
# A policy change takes a little while to reach the API server's hook; ask every 3 s for up to
# 90 s and say how long it took. A change that NEVER takes effect still fails the trial.
T0=$(date +%s); OUT=""
for _ in $(seq 1 30); do
  OUT="$(pod t6 kaval-staging "$SIGNED_TAG")" || true   # a refusal is a non-zero answer
  [ "$(answer "$OUT")" = refused ] && break
  sleep 3
done
echo "  (the changed policy took effect after $(( $(date +%s) - T0 )) s)"
expect "signed release, policy expects ci.yml"            refused "$OUT"
apply_policy "/'${UNSIGNED_TAG}',/d"
expect "signed release again, back on the real policy"    allowed "$(pod t7 kaval-staging "$SIGNED_TAG")"

hook_back() { # wait until Kyverno has registered its hook again; print how long that took
  local t0 n; t0=$(date +%s)
  for _ in $(seq 1 60); do
    n="$({ k get validatingwebhookconfigurations kyverno-resource-validating-webhook-cfg -o name 2>/dev/null || true; } | wc -l)"
    [ "$n" = 1 ] && break
    sleep 3
  done
  sleep 3
  echo "  (the hook was back after $(( $(date +%s) - t0 )) s)"
}
plain_pod() { printf 'apiVersion: v1\nkind: Pod\nmetadata: {name: %s, namespace: kube-system}\nspec:\n  containers: [{name: c, image: "nginx:1.27"}]\n' "$1" | k apply --dry-run=server -f - 2>&1 | scrub; }
OTHER_TAG="$SIGNED_TAG"   # same release, another image: Kyverno has not checked it yet

echo "== the checker crashed (killed hard: its hook stays registered, nobody answers)"
k -n kyverno delete pod -l app.kubernetes.io/component=admission-controller --grace-period=0 --force >/dev/null 2>&1
sleep 1
expect "signed release, checker crashed"                  unreachable "$(pod d1 kaval-staging "$SIGNED_TAG")"
expect "unsigned release, checker crashed"                unreachable "$(pod d2 kaval-staging "$UNSIGNED_TAG")"
expect "the same unsigned image elsewhere"                allowed      "$(pod d3 elsewhere "$UNSIGNED_TAG")"
expect "a plain pod in kube-system"                       allowed      "$(plain_pod d4)"
echo "== the emergency off-switch: delete both of Kyverno's hooks by hand while it is down"
k delete validatingwebhookconfiguration kyverno-resource-validating-webhook-cfg >/dev/null
k delete mutatingwebhookconfiguration kyverno-resource-mutating-webhook-cfg >/dev/null
sleep 2
expect "signed release, both hooks deleted by hand"             allowed "$(pod e1 kaval-staging "$SIGNED_TAG")"
k -n kyverno rollout status deploy/kyverno-admission-controller --timeout=180s >/dev/null
hook_back
expect "signed release after Kyverno re-registered"       allowed "$(pod e2 kaval-staging "$SIGNED_TAG")"
expect "unsigned release after Kyverno re-registered"     refused "$(pod e3 kaval-staging "$UNSIGNED_TAG")"

echo "== the checker is stopped cleanly (Kyverno removes its own hook: measured, not wished for)"
k -n kyverno scale deploy kyverno-admission-controller --replicas=0 >/dev/null
for _ in $(seq 1 30); do
  [ -z "$(k -n kyverno get pods -l app.kubernetes.io/component=admission-controller -o name 2>/dev/null)" ] && break
  sleep 2
done
sleep 5
expect "unsigned release, checker stopped cleanly"        allowed "$(pod c1 kaval-staging "$UNSIGNED_TAG")"
k -n kyverno scale deploy kyverno-admission-controller --replicas=1 >/dev/null
k -n kyverno rollout status deploy/kyverno-admission-controller --timeout=180s >/dev/null
hook_back
expect "signed release after the restart"                 allowed "$(pod r1 kaval-staging "$SIGNED_TAG")"
expect "unsigned release after the restart"               refused "$(pod r2 kaval-staging "$UNSIGNED_TAG")"

echo "== Sigstore cannot be reached (its addresses point nowhere, for the checker only)"
k -n kyverno patch deploy kyverno-admission-controller --type=json -p '[{"op":"add","path":"/spec/template/spec/hostAliases","value":[{"ip":"127.0.0.1","hostnames":["rekor.sigstore.dev","fulcio.sigstore.dev","tuf-repo-cdn.sigstore.dev","ctfe.sigstore.dev"]}]}]' >/dev/null
k -n kyverno rollout status deploy/kyverno-admission-controller --timeout=180s >/dev/null
hook_back
expect "signed release, Sigstore unreachable"             refused "$(pod s1 kaval-staging "$OTHER_TAG")"
expect "unsigned release, Sigstore unreachable"           refused "$(pod s2 kaval-staging "$UNSIGNED_TAG")"
expect "the same unsigned image elsewhere"                allowed "$(pod s3 elsewhere "$UNSIGNED_TAG")"
k -n kyverno patch deploy kyverno-admission-controller --type=json -p '[{"op":"remove","path":"/spec/template/spec/hostAliases"}]' >/dev/null
k -n kyverno rollout status deploy/kyverno-admission-controller --timeout=180s >/dev/null
hook_back
expect "signed release once Sigstore is reachable again"  allowed "$(pod s4 kaval-staging "$SIGNED_TAG")"

echo "== memory the checker uses"
sleep 20
k top pod -n kyverno 2>/dev/null | tail -n +1 || echo "  (metrics-server not ready)"

if [ "$FAILS" -ne 0 ]; then echo "TRIAL FAILED: $FAILS answer(s) were not the expected one"; exit 1; fi
echo "TRIAL PASSED"
