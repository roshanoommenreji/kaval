"""Prove the cluster's image checker on a live node, step by step (KAV-143, Lab 97).

    python scripts/ops/checker_proof.py <prod|staging> <step> [--yes]

Steps, in the order of a proof session:

    state      read-only: the policy, the Kaval pods, the checker's pods and memory, the node's
    alerts     read-only: whether Prometheus loaded the checker's two alert rules
    plant      (laptop) give one existing image a second, unsigned name in the registry
    signed     start a throwaway pod from a SIGNED release        -> expect: accepted
    unsigned   start a throwaway pod from the planted image       -> expect: REFUSED
    frozen     start a throwaway pod from an UNSIGNED release on the frozen list -> expect: accepted
    restart    stop the checker's container so it restarts IN PLACE (same pod, same address), wait
               for it and its hooks to come back; shows its start time before and after (KAV-144)
    clean     delete every throwaway pod
    unplant    (laptop) remove the planted name from the registry

The pods change nothing that matters: each only sleeps, holds no secret, and is labelled
`proof=kav143` so `clean` removes exactly those. The commands run on the node over Session Manager
(the node's own kubectl), so no cluster address or login ever sits on the laptop. Steps that create
or delete something need `--yes`: a typed go from the owner covers them, a stray run does not.
Output has every 12-digit number replaced, because the registry address holds the account number.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import time

REGION = "ap-south-1"
SIGNED_TAG = "sha-152b59e"  # built and signed by release.yml on main; never promoted
PLANTED_TAG = "unsigned-trial"
BASE_TAG = "sha-791c44d"  # unsigned, and a DIFFERENT image from SIGNED_TAG (plant, frozen)
LABEL = "proof=kav143"
REPO = "kaval/gateway"
NAMESPACES = {"prod": "kaval-prod", "staging": "kaval-staging"}
LAPTOP_STEPS = ("plant", "unplant")
MUTATING = ("plant", "signed", "unsigned", "frozen", "restart", "clean", "unplant")
STEPS = ("state", "alerts", "plant", "signed", "unsigned", "frozen", "restart", "clean", "unplant")
CHECKER_METRICS = "/api/v1/namespaces/kyverno/services/kyverno-svc-metrics:8000/proxy/metrics"


def scrub(text: str) -> str:
    """Replace anything shaped like an AWS account number (it is part of the registry address)."""
    return re.sub(r"(?<![0-9])[0-9]{12}(?![0-9])", "<acct>", text)


def pod_manifest(name: str, namespace: str, registry_var: str, tag: str) -> str:
    """One pod that only sleeps. `registry_var` is the shell variable the remote script fills in."""
    return (
        "apiVersion: v1\nkind: Pod\nmetadata:\n"
        f"  name: {name}\n  namespace: {namespace}\n  labels: {{proof: kav143}}\n"
        "spec:\n  imagePullSecrets: [{name: ecr-cred}]\n  automountServiceAccountToken: false\n"
        "  containers:\n  - name: c\n"
        f"    image: ${{{registry_var}}}/{REPO}:{tag}\n"
        '    command: ["sleep","600"]\n'
        "    resources: {requests: {cpu: 10m, memory: 16Mi}, limits: {memory: 64Mi}}\n"
    )


def remote_script(step: str, namespace: str, env: str) -> str:
    """The shell that runs on the node for a step that needs the cluster."""
    head = (
        "export KUBECONFIG=/etc/rancher/k3s/k3s.yaml PATH=$PATH:/usr/local/bin\n"
        "REG=$(kubectl get cm kaval-registry -n flux-system -o jsonpath='{.data.registry}')\n"
        f"NS={namespace}\n"
    )
    def mk(name: str, tag: str) -> str:
        manifest = pod_manifest(name, "$NS", "REG", tag)
        return f"cat <<EOF | kubectl apply -f - 2>&1 | cut -c1-600\n{manifest}EOF\n"

    policy_fields = (
        "'{.metadata.name} ready={.status.conditionStatus.ready}"
        " actions={.spec.validationActions} failurePolicy={.spec.failurePolicy}"
        " timeout={.spec.webhookConfiguration.timeoutSeconds}{\"\\n\"}'"
    )
    body = {
        "state": (
            f"kubectl get imagevalidatingpolicy kaval-images-signed -o jsonpath={policy_fields}\n"
            "echo '--- Kaval pods by state'; kubectl get pods -n $NS --no-headers"
            " | awk '{print $3}' | sort | uniq -c\n"
            "echo '--- checker'; kubectl get pods -n kyverno --no-headers\n"
            "kubectl top pod -n kyverno --no-headers 2>/dev/null\n"
            "echo '--- hooks'; kubectl get validatingwebhookconfiguration,"
            "mutatingwebhookconfiguration -o name | grep -i kyverno-resource\n"
            "echo '--- the checker login (age)'; kubectl get secret ecr-cred -n kyverno"
            " -o jsonpath='{.metadata.creationTimestamp}{\"\\n\"}'\n"
            "echo '--- node memory (MiB)'; free -m | awk 'NR<=2'\n"
            "kubectl top node --no-headers 2>/dev/null\n"
        ),
        "alerts": (
            f"kubectl -n $NS exec deploy/kaval-{env}-prometheus -- wget -qO- "
            "'http://localhost:9090/api/v1/rules' | python3 -c \""
            "import json,sys\n"
            "for g in json.load(sys.stdin)['data']['groups']:\n"
            "    for r in g['rules']:\n"
            "        if r.get('name') in ('ImageCheckerDown','UnsignedImageSeen'):\n"
            "            print(r['name'], r['health'], r['state'])\"\n"
        ),
        "signed": (
            mk("proof-signed", SIGNED_TAG)
            + "sleep 20; kubectl get pod proof-signed -n $NS --no-headers"
            " | awk '{print \"signed pod:\",$2,$3}'\n"
        ),
        "unsigned": (
            mk("proof-unsigned", PLANTED_TAG)
            + "sleep 3; echo '--- is the pod there?'; kubectl get pod proof-unsigned -n $NS"
            " --no-headers 2>&1 | cut -c1-120\n"
            "echo '--- counters'; kubectl get --raw " + CHECKER_METRICS + " | grep -E "
            "'^kyverno_image_validating_policy_results_total' | grep kaval-images-signed"
            " | sed -E 's/otel_scope[a-z_]*=\"[^\"]*\",//g' | cut -c1-260\n"
        ),
        # KAV-144: a restart in place is what hid a refusal from the alert. Deleting the pod would
        # not show it (a new pod has a new address, which the alert always saw); stopping the
        # container makes the kubelet start it again inside the same pod. A clean stop removes
        # the checker's hooks for a few seconds, so this waits for them before it returns.
        "restart": (
            "show() { kubectl get pods -n kyverno -o jsonpath='{range .items[*]}{.metadata.name}"
            " ip={.status.podIP} restarts={.status.containerStatuses[0].restartCount}"
            "{\"\\n\"}{end}'; kubectl get --raw " + CHECKER_METRICS
            + " 2>/dev/null | grep -E '^process_start_time_seconds' ; }\n"
            # k3s installed from its binary has no `crictl` on the PATH, only `k3s crictl`
            # (found live on staging, 2026-11-13); a stock node may have either.
            "export HOME=${HOME:-/root}\n"
            "CRI=$(command -v crictl || echo 'k3s crictl')\n"
            "echo '--- before'; show\n"
            "OLD=$($CRI ps --name '^kyverno$' --label io.kubernetes.pod.namespace=kyverno -q"
            " | head -1)\n"
            "[ -n \"$OLD\" ] || { echo 'no running checker container: nothing restarted';"
            " exit 1; }\n"
            "$CRI stop \"$OLD\" >/dev/null"
            " && echo 'stopped the checker container (the pod stays)'\n"
            "for i in $(seq 1 40); do sleep 3\n"
            "  NEW=$($CRI ps --name '^kyverno$' --label io.kubernetes.pod.namespace=kyverno"
            " -q | head -1)\n"
            "  [ -n \"$NEW\" ] && [ \"$NEW\" != \"$OLD\" ] && kubectl get"
            " validatingwebhookconfiguration kyverno-resource-validating-webhook-cfg"
            " >/dev/null 2>&1 && break\n"
            "done\n"
            "echo \"--- after ($((i * 3)) s)\"; show\n"
            "echo '--- hooks'; kubectl get validatingwebhookconfiguration,"
            "mutatingwebhookconfiguration -o name | grep -i kyverno-resource\n"
        ),
        "frozen": (
            mk("proof-frozen", BASE_TAG)  # unsigned, listed: never checked (ADR-0096)
            + "sleep 20; kubectl get pod proof-frozen -n $NS --no-headers"
            " | awk '{print \"frozen pod:\",$2,$3}'\n"
        ),
        "clean": (f"kubectl delete pod -n $NS -l {LABEL} --ignore-not-found --wait=false"
                  " 2>&1 | cut -c1-120\n"),
    }[step]
    return head + body


def _aws(*args: str) -> str:
    env = dict(os.environ, AWS_PROFILE=os.environ.get("AWS_PROFILE", "kaval"), AWS_REGION=REGION)
    done = subprocess.run(["aws", *args], capture_output=True, text=True, env=env,
                          encoding="utf-8", check=False)
    return done.stdout.strip()


def run_on_node(env: str, script: str) -> str:
    node = _aws("ec2", "describe-instances", "--filters", f"Name=tag:Name,Values=kaval-{env}",
                "Name=instance-state-name,Values=running", "--query",
                "Reservations[0].Instances[0].InstanceId", "--output", "text")
    if not node or node == "None":
        return f"kaval-{env} has no running server: nothing to prove (is it parked?)"
    packed = base64.b64encode(script.encode("utf-8")).decode("ascii")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump({"commands": [f"echo {packed} | base64 -d > /tmp/checker-proof.sh; "
                                "bash /tmp/checker-proof.sh"]}, f)
    cid = _aws("ssm", "send-command", "--instance-ids", node, "--document-name",
               "AWS-RunShellScript", "--parameters", "file://" + f.name.replace("\\", "/"),
               "--query", "Command.CommandId", "--output", "text")
    os.unlink(f.name)
    status = ""
    for _ in range(60):
        time.sleep(3)
        status = _aws("ssm", "get-command-invocation", "--command-id", cid, "--instance-id", node,
                      "--query", "Status", "--output", "text")
        if status in ("Success", "Failed", "Cancelled", "TimedOut"):
            break
    out = _aws("ssm", "get-command-invocation", "--command-id", cid, "--instance-id", node,
               "--query", "StandardOutputContent", "--output", "text")
    err = _aws("ssm", "get-command-invocation", "--command-id", cid, "--instance-id", node,
               "--query", "StandardErrorContent", "--output", "text")
    return out + (f"\n[stderr]\n{err}" if err and err != "None" else "") + f"\n[status] {status}"


def _digest(tag: str) -> str:
    """The image digest a tag points at in the registry ('' when it cannot be read)."""
    out = _aws("ecr", "describe-images", "--repository-name", REPO, "--image-ids",
               f"imageTag={tag}", "--query", "imageDetails[0].imageDigest", "--output", "text")
    return out if out.startswith("sha256:") else ""


def laptop_step(step: str) -> str:
    """plant / unplant: the registry gives the signed release's image a second name, or drops it."""
    if step == "unplant":
        return _aws("ecr", "batch-delete-image", "--repository-name", REPO,
                    "--image-ids", f"imageTag={PLANTED_TAG}", "--output", "text") or "removed"
    base, signed = _digest(BASE_TAG), _digest(SIGNED_TAG)
    if not base or base == signed:
        # A signature belongs to a digest, not a tag. Two releases built from unchanged code can be
        # the very same image: planting from one of those would plant a SIGNED image (found live on
        # production, KAV-143: sha-d1058eb and sha-152b59e are one image, so the "unsigned" pod was
        # correctly accepted).
        return f"refusing to plant: {BASE_TAG} is the same image as {SIGNED_TAG}, or unreadable"
    manifest = _aws("ecr", "batch-get-image", "--repository-name", REPO,
                    "--image-ids", f"imageTag={BASE_TAG}",
                    "--query", "images[0].imageManifest", "--output", "text")
    if not manifest or manifest == "None":
        return "could not read the base image's manifest: nothing planted"
    mime = json.loads(manifest).get("mediaType", "application/vnd.oci.image.index.v1+json")
    out = _aws("ecr", "put-image", "--repository-name", REPO, "--image-tag", PLANTED_TAG,
               "--image-manifest", manifest, "--image-manifest-media-type", mime,
               "--query", "image.imageId.imageTag", "--output", "text")
    return f"planted {out or '(already there)'}"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("env", choices=sorted(NAMESPACES))
    ap.add_argument("step", choices=STEPS)
    ap.add_argument("--yes", action="store_true", help="needed for steps that create or delete")
    args = ap.parse_args(argv)
    if args.step in MUTATING and not args.yes:
        print(f"'{args.step}' creates or deletes something: add --yes once the owner typed the go.")
        return 2
    if args.step in LAPTOP_STEPS:
        print(scrub(laptop_step(args.step)))
        return 0
    if args.step == "frozen":
        # KAV-146: a "frozen pass" proves nothing if the image would pass on its signature anyway
        # (sha-d1058eb's gateway is the signed sha-152b59e's, which hid a list that never worked).
        base, signed = _digest(BASE_TAG), _digest(SIGNED_TAG)
        if not base or base == signed:
            print(f"refusing: {BASE_TAG} is the same image as {SIGNED_TAG}, or unreadable: "
                  "a pass would prove the signature, not the frozen list. Nothing started.")
            return 1
    print(scrub(run_on_node(args.env, remote_script(args.step, NAMESPACES[args.env], args.env))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
