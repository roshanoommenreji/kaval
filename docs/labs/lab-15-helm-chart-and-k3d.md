# Lab 15 — The Helm chart, deployed to a real k3d cluster

**Phase:** 3 · **Time:** ~45 min · **Cost:** $0 (the dev server you already have running)

Phase 2 proved the agent loop against Docker Compose. This lab deploys the same loop —
Postgres, gateway, the correlate loop — through the real Helm chart, onto a real Kubernetes
cluster, for the first time. The design is
[ADR-0020](../adr/0020-the-helm-chart-and-the-local-k3d-environment.md).

## Prerequisites

- [Lab 14](lab-14-bedrock-escalation.md) done
- `make devbox-up`
- `k3d`, `kubectl`, `helm` on the dev server (Step 1 installs them if they're missing — a
  devbox created after this lab shipped already has them, via `user_data.sh.tftpl`)

---

## Step 1 — install the tools, checksum-verified

```bash
make devbox-ssh
```

Then, on the dev server (skip anything already installed):

```bash
K3D_VERSION=v5.9.0
K3D_SHA256=03cde5cf23e6e8e67de5a039ecf26e5b85aca82fba3e5d13dadf904cd218a250
curl -fsSLo /tmp/k3d "https://github.com/k3d-io/k3d/releases/download/${K3D_VERSION}/k3d-linux-arm64"
echo "${K3D_SHA256}  /tmp/k3d" | sha256sum --check --strict
sudo install -m 0755 /tmp/k3d /usr/local/bin/k3d

KUBECTL_VERSION=v1.37.1
KUBECTL_SHA256=ff749f4b78d9c4f1ec87307df9b50119ed819e2094aa9810cb9acffc3286c8c7
curl -fsSLo /tmp/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/arm64/kubectl"
echo "${KUBECTL_SHA256}  /tmp/kubectl" | sha256sum --check --strict
sudo install -m 0755 /tmp/kubectl /usr/local/bin/kubectl

HELM_VERSION=v4.3.0
HELM_SHA256=31c5794dd55c66a51e6b7d2e2ac7a114ae8b1de41ff1d9ba51748ac973b06a08
curl -fsSLo /tmp/helm.tgz "https://get.helm.sh/helm-${HELM_VERSION}-linux-arm64.tar.gz"
echo "${HELM_SHA256}  /tmp/helm.tgz" | sha256sum --check --strict
tar -xzf /tmp/helm.tgz -C /tmp linux-arm64/helm
sudo install -m 0755 /tmp/linux-arm64/helm /usr/local/bin/helm
```

## Step 2 — create the cluster

```bash
k3d cluster create kaval-local --wait --timeout 120s
kubectl get nodes
```

One node, `Ready`. ~800 MB RAM used, comfortably inside the dev server's 3.7 GB.

## Step 3 — lint and render the chart, before deploying anything

From your laptop, in the repo:

```bash
helm lint deploy/charts/kaval -f deploy/environments/local/values.yaml
helm template kaval-local deploy/charts/kaval -f deploy/environments/local/values.yaml
```

Read the rendered output once. `helm lint` passing is not the same as the YAML being correct —
see **What actually happened** below for exactly why that distinction mattered here.

## Step 4 — build the images and get them into the cluster

```bash
# from your laptop, against the dev server's own Docker daemon
DOCKER_API_VERSION=1.44 docker --context kaval-devbox compose build gateway agent signals
```

Then, on the dev server:

```bash
k3d image import kaval/gateway:dev kaval/agent:dev kaval/collector:dev -c kaval-local
```

No registry involved — the same Docker daemon builds the images and runs k3d's nodes, so
`k3d image import` just hands them across directly.

## Step 5 — deploy

```bash
helm install kaval-local deploy/charts/kaval -f deploy/environments/local/values.yaml \
  --wait --timeout 120s
kubectl get pods
```

Four pods, all `Running`, `1/1`. `0` restarts, if you're on the version of the chart that's
actually in this repo today — see below for why an earlier version of this lab would have
shown `1`.

## Step 6 — prove the loop runs for real

Write one real incident's signals directly into the cluster's own Postgres:

```bash
kubectl run signals-test --rm -i --restart=Never \
  --image=kaval/collector:dev --image-pull-policy=IfNotPresent \
  --env="POSTGRES_HOST=kaval-local-postgres" --env="POSTGRES_PORT=5432" \
  --env="POSTGRES_DB=kaval" --env="POSTGRES_USER=kaval" --env="POSTGRES_PASSWORD=kaval-local-dev" \
  -- oom-crashloop
```

Then watch the deployed agent's own logs:

```bash
kubectl logs -l app.kubernetes.io/component=agent -f
```

Within one `--every 30` tick, it opens the incident itself — not a laptop process, the
Kubernetes-deployed one.

## Step 7 — confirm the gateway sees the same thing

```bash
kubectl port-forward svc/kaval-local-gateway 8000:8000 &
curl -s http://localhost:8000/healthz
curl -s http://localhost:8000/v1/incidents
```

`/healthz` reports `"status":"degraded"` with `"ollama":{"ok":false,...}` — expected, see
ADR-0020's Ollama section. `/v1/incidents` shows the incident Step 6 opened.

## Done when

- [x] `k3d`/`kubectl`/`helm` installed, checksum-verified
- [x] `helm lint` and `helm template` both clean
- [x] Four pods (`postgres`, `gateway`, `agent`, plus the one-shot `migrate` Job) running in a
      real k3d cluster
- [x] A real incident opened by the Kubernetes-deployed agent, not the laptop
- [x] The gateway's `/v1/incidents` shows the same incident

## What actually happened, live (2026-10-01)

Two real bugs, not hypothetical ones, found by actually deploying rather than just rendering:

**The first `helm install` left the migrate Job stuck forever.** `kubectl get pods` showed
`kaval-local-migrate-1-tvh4v` at `Init:0/1` with **no postgres pod anywhere** — the migrate
Job's hook was `pre-install`, which runs *before any other resource in the release exists*,
including the Postgres its own `wait-for-postgres` initContainer was waiting for. Changed to
`post-install,pre-upgrade`; the next install completed in under 30 seconds.

**The agent crashed once, on its very first tick, racing Postgres's own startup** —
`kubectl get pods` showed `RESTARTS 1 (15s ago)` on a pod that had only existed for 29 seconds.
`kubectl logs --previous` showed an unhandled `psycopg.OperationalError: connection to server
... failed: Connection refused` — `correlate.py`'s `--every` loop doesn't catch a connection
failure and retry, it exits, and Kubernetes' own restart silently recovered it. Easy to miss as
"just a restart" rather than a real race. Gave the agent Deployment the same
`wait-for-postgres` initContainer the migrate Job already needed; the next deploy showed zero
restarts.

**A third bug was caught before it ever reached a live cluster**, while just reading
`helm template`'s rendered output in Step 3: a `{{- -}}` trim pair in `agent.yaml` had eaten
the newline between a top-of-file comment block and `apiVersion: apps/v1`, merging them onto
one line and turning the entire `apiVersion` key into part of the YAML comment. `helm lint`
did not catch this — only reading the rendered output did. Fixed, and now automated: CI gained
a `helm` job running `helm template` through **kubeconform**, confirmed (by deliberately
reintroducing the exact bug and re-running the check) to fail with `missing 'apiVersion' key`
— the kind of thing this lab's Step 3 asks you to read by eye precisely because nothing
automated was catching it before today.

`/healthz`, live: `{"status":"degraded","version":"0.1.0","postgres":{"ok":true,"detail":
"migrated to <head revision>"},"ollama":{"ok":false,"detail":"UnsupportedProtocol"}}` — exactly
as designed. The agent's own log line on the tick that found the signals:
`opened    oom_killed:k8s:kaval-demo/checkout  high  6 signals`.
