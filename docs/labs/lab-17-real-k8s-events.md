# Lab 17 — Real Kubernetes events as signals

**Phase:** 3 · **Time:** ~40 min · **Cost:** $0 (the dev server and k3d cluster you already have)

Every signal Kaval has reacted to through Lab 16 was typed in by hand. This lab gives the
collector a second source — real Kubernetes events, polled continuously — so breaking
something for real in `kaval-demo` is enough, on its own, to start the whole loop. The design
is [ADR-0022](../adr/0022-real-kubernetes-events-as-signals.md).

## Prerequisites

- [Lab 16](lab-16-executor-and-scoped-rbac.md) done
- `make dev-tunnel` running, in its own terminal

---

## Step 1 — build the collector image

```bash
docker --context kaval-devbox compose build collector
```

## Step 2 — lint and render, before deploying anything

```bash
helm lint deploy/charts/kaval -f deploy/environments/local/values.yaml
helm template kaval-local deploy/charts/kaval -f deploy/environments/local/values.yaml \
  | kubeconform -strict -kubernetes-version 1.35.5 -summary -
```

Expect `18/18` resources valid — the collector's `ServiceAccount`, `Role`, `RoleBinding` and
`Deployment`, alongside the 14 from Lab 16.

## Step 3 — get the image into k3d, upgrade the release

```bash
k3d image import kaval/collector:dev -c kaval-local
helm upgrade kaval-local deploy/charts/kaval -f deploy/environments/local/values.yaml \
  --wait --timeout 180s
```

## Step 4 — prove the RBAC claim

```bash
kubectl auth can-i list events --as=system:serviceaccount:default:kaval-local-collector -n kaval-demo   # yes
kubectl auth can-i delete pods --as=system:serviceaccount:default:kaval-local-collector -n kaval-demo   # no
kubectl auth can-i list events --as=system:serviceaccount:default:kaval-local-collector -n default      # no
```

The collector can watch events in `kaval-demo`. It cannot act on anything, anywhere — not
even in the one namespace it's allowed to look at.

## Step 5 — break something for real, with no hand-written signal

```bash
kubectl run crashy --image=busybox:1.36 -n kaval-demo --restart=Always -- sh -c "exit 1"
```

A command that exits immediately makes Kubernetes restart it, back off, and restart it again —
a real `BackOff` event, the real thing `kaval_collector.synthetic`'s `oom-crashloop` scenario
only ever faked.

## Step 6 — watch it happen, unattended

```bash
kubectl logs -l app.kubernetes.io/component=collector -f
```

Within one poll (`collector.watchEverySeconds`, 15s locally), a `pod_back_off` line should
appear, naming the real pod. Then check the agent noticed on its own:

```bash
kubectl logs -l app.kubernetes.io/component=agent --tail=5
```

## Done when

- [x] `helm lint`/`helm template | kubeconform` clean, 18/18 resources
- [x] The three `kubectl auth can-i` checks return exactly `yes, no, no`
- [x] A real, unscripted pod failure produces a real signal within one poll, with no row
      written by hand
- [x] The deployed agent opens a real incident from it, on its own next tick

---

## What actually happened, live (2026-10-02)
