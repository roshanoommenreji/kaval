# Lab 100 — The cluster half of the access review

**Phase:** 8 (hardening) · **Story:** `KAV-154` (cluster half; the AWS half is still open) · **Evidence:** [access-review.md](../architecture/access-review.md) · **Cost:** $0, nothing is started

## What you will build

A list of every permission Kaval's parts hold inside Kubernetes, taken from the chart exactly as it is rendered for each environment, and a short list of findings. No cluster is needed: Flux builds the cluster from this chart, so reading the chart is reading what the cluster will get.

You need the repository, Helm 3 and the project's Python environment (`make sync`).

## Why read the chart and not the cluster

Real teams do both. Reading the rendered chart is the cheap half: it runs on every laptop and can run on every pull request, and it catches a role that is too wide *before* it is deployed. Reading the live cluster catches what git does not know about: a binding someone made by hand. Kaval's cluster is rebuilt from git, so the first half carries most of the weight; the second is one read-only command in a production session.

## Part 1 — Render the chart as production gets it

```bash
helm template kaval deploy/charts/kaval -n kaval \
  -f deploy/environments/prod/values.yaml > /tmp/prod.yaml
grep -c "^kind: " /tmp/prod.yaml
```

This is the same YAML Flux applies. Nothing is contacted.

## Part 2 — Ask who holds what

```bash
python scripts/dev/rbac_scan.py < /tmp/prod.yaml
```

Expected (2026-11-15):

```
WORKLOADS  (name | service account | mounts a token | account has a role)
  Deployment/kaval-agent | default | no | no
  Deployment/kaval-collector | kaval-collector | yes | yes
  Deployment/kaval-collector-cost | kaval-cost | yes | yes
  Deployment/kaval-collector-prometheus | default | no | no
  Deployment/kaval-diagnoser | default | no | no
  Deployment/kaval-executor | kaval-executor | yes | yes
  Deployment/kaval-gateway | default | yes | no
  Deployment/kaval-ollama | default | no | no
  Deployment/kaval-prometheus | kaval-prometheus | yes | yes
  Job/kaval-migrate-1 | default | yes | no
BINDINGS  (who | where | role)
  ...
WILDCARDS: none
CAN READ SECRETS: none
TOKENS NOTHING NEEDS:
  Deployment/kaval-gateway mounts the token of 'default', which is bound to no role
  Job/kaval-migrate-1 mounts the token of 'default', which is bound to no role
```

Read it in this order:

1. **`WILDCARDS` and `CAN READ SECRETS` must both say `none`.** The script exits 1 if either does not, so it can be used as a check.
2. **The agent's line:** `default | no | no`. No role and no token. That is the project's rule 3, seen in the output.
3. **`TOKENS NOTHING NEEDS`:** a pod that carries a token for an account bound to nothing. Harmless today and still a credential nobody uses.

## Part 3 — The same for staging

```bash
helm template kaval deploy/charts/kaval -n kaval \
  -f deploy/environments/staging/values.yaml | python scripts/dev/rbac_scan.py
```

Staging adds four chaos jobs and their account, `kaval-chaos-runner`. Check that this account appears **only** here and not in Part 2's output.

## Part 4 — The negative case

A check that never says no proves nothing. The tests feed it a chart with a wildcard, a chart with a rule that reads Secrets, and a pod with a spare token:

```bash
python -m pytest scripts/dev/test_rbac_scan.py -q
```

Five pass. To see it by hand, add `- apiGroups: ["*"]`, `resources: ["*"]`, `verbs: ["*"]` to the executor's role in a scratch copy of the chart, render it and run the scan: it prints the rule under `WILDCARDS` and exits 1.

## Part 5 — What is not in the chart

Three things act in the cluster without a role from Kaval's chart. Read them, do not scan them:

| Thing | Where to look | What to check |
|---|---|---|
| Flux | `deploy/gitops/<env>/*.yaml` | No `serviceAccountName`, so it applies with full rights. Accepted for a one-tenant cluster |
| The image checker (Kyverno) | `deploy/gitops/<env>/checker-helmrelease.yaml` | Chart version pinned; the background, cleanup and reports controllers are off |
| The server's own scripts | `infra/modules/node/user_data.sh.tftpl` | They use the cluster's administrator file; the server has no inbound port |

## What this found

Recorded with the risk and the decision for each in [access-review.md](../architecture/access-review.md): one finding to fix (C-1, the two spare tokens) and four accepted with a reason.

## Still to do

- The fix for C-1: `automountServiceAccountToken: false` on the gateway and the migration job. A chart change, so staging first, then production.
- The live check in a production session, read-only: `kubectl auth can-i --list --as=system:serviceaccount:kaval:<account>` for each account, and `kubectl get rolebindings,clusterrolebindings -A` compared with Part 2.
- The AWS half.

## Clean up

Delete `/tmp/prod.yaml`. Nothing else was created.
