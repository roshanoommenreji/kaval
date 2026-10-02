# Lab 18 — Promotion mechanics rehearsed on k3d

**Phase:** 3 · **Time:** ~30 min · **Cost:** $0 (the dev server and k3d cluster you already have)

Every lab so far has deployed one release, `kaval-local`. This lab deploys two more —
`kaval-staging` and `kaval-prod` — into the *same* k3d cluster, to rehearse the mechanics
Phase 4's real second cluster will need: build an image once, promote the unchanged artifact,
and measure how fast a `helm rollback` restores a broken release. The design, and why this
is a rehearsal rather than the real thing, is [ADR-0023](../adr/0023-promotion-rehearsal-on-k3d.md).

## Prerequisites

- [Lab 17](lab-17-real-k8s-events.md) done
- `make dev-tunnel` running, in its own terminal, if you want to watch the gateway yourself

---

## Step 1 — build once, tag by commit, not by `:dev`

```bash
docker --context kaval-devbox compose build gateway agent executor signals   # signals = collector
SHA=$(git rev-parse --short HEAD)
ssh kaval-devbox "
  docker tag kaval/gateway:dev kaval/gateway:sha-$SHA
  docker tag kaval/agent:dev kaval/agent:sha-$SHA
  docker tag kaval/executor:dev kaval/executor:sha-$SHA
  docker tag kaval/collector:dev kaval/collector:sha-$SHA
"
k3d image import kaval/gateway:sha-$SHA kaval/agent:sha-$SHA kaval/executor:sha-$SHA \
  kaval/collector:sha-$SHA -c kaval-local
```

A floating `:dev` tag can't prove "the same artifact" twice — a second build with the same tag
could silently differ. A commit-SHA tag can.

## Step 2 — lint and render both new environments

```bash
helm template kaval-staging deploy/charts/kaval \
  -f deploy/charts/kaval/values.yaml -f deploy/environments/staging/values.yaml \
  --set gateway.image.tag=sha-$SHA --set agent.image.tag=sha-$SHA \
  --set executor.image.tag=sha-$SHA --set collector.image.tag=sha-$SHA \
  -n kaval-staging | kubeconform -strict -summary -

helm template kaval-prod deploy/charts/kaval \
  -f deploy/charts/kaval/values.yaml -f deploy/environments/prod/values.yaml \
  --set gateway.image.tag=sha-$SHA --set agent.image.tag=sha-$SHA \
  --set executor.image.tag=sha-$SHA --set collector.image.tag=sha-$SHA \
  -n kaval-prod | kubeconform -strict -summary -
```

Same 17 resources both times — `staging/values.yaml` and `prod/values.yaml` change *values*
only (replica count, poll intervals, the demo namespace name), never which resources exist.

## Step 3 — deploy staging first

```bash
helm upgrade --install kaval-staging deploy/charts/kaval \
  -f deploy/charts/kaval/values.yaml -f deploy/environments/staging/values.yaml \
  --set gateway.image.tag=sha-$SHA --set agent.image.tag=sha-$SHA \
  --set executor.image.tag=sha-$SHA --set collector.image.tag=sha-$SHA \
  -n kaval-staging --create-namespace --wait --timeout 180s

kubectl port-forward svc/kaval-staging-gateway 18001:8000 -n kaval-staging &
curl -s http://localhost:18001/healthz
```

Expect `postgres.ok: true`; `ollama.ok: false` is the same expected state every prior lab has
recorded (no chart yet deploys Ollama) — not a new failure.

## Step 4 — promote the identical artifact to prod, never rebuild

```bash
helm upgrade --install kaval-prod deploy/charts/kaval \
  -f deploy/charts/kaval/values.yaml -f deploy/environments/prod/values.yaml \
  --set gateway.image.tag=sha-$SHA --set agent.image.tag=sha-$SHA \
  --set executor.image.tag=sha-$SHA --set collector.image.tag=sha-$SHA \
  -n kaval-prod --create-namespace --wait --timeout 180s
```

Prove it's the same bytes, not just the same tag text:

```bash
kubectl get pod -n kaval-staging <staging-gateway-pod> -o jsonpath='{.status.containerStatuses[0].imageID}'
kubectl get pod -n kaval-prod <prod-gateway-pod> -o jsonpath='{.status.containerStatuses[0].imageID}'
```

Both `imageID`s should match exactly.

## Step 5 — break something in prod on purpose

```bash
helm upgrade kaval-prod deploy/charts/kaval \
  -f deploy/charts/kaval/values.yaml -f deploy/environments/prod/values.yaml \
  --set gateway.image.tag=sha-$SHA --set agent.image.tag=sha-$SHA \
  --set executor.image.tag=sha-does-not-exist --set collector.image.tag=sha-$SHA \
  -n kaval-prod --timeout 30s
kubectl get pods -n kaval-prod
```

(Breaking `gateway`'s tag instead teaches a different, equally real lesson — see "What
actually happened" below.)

## Step 6 — roll back, and time it

```bash
helm history kaval-prod -n kaval-prod   # find the last "deployed" or "superseded" good revision
START=$(date +%s.%N)
helm rollback kaval-prod <good-revision> -n kaval-prod --wait --timeout 60s
END=$(date +%s.%N)
echo "$END - $START" | bc
```

## Step 7 — clean up

```bash
helm uninstall kaval-staging -n kaval-staging
helm uninstall kaval-prod -n kaval-prod
kubectl delete namespace kaval-staging kaval-prod kaval-demo-staging kaval-demo-prod
```

These two releases are a rehearsal, not infrastructure meant to persist — `kaval-local` is
Phase 3's real, ongoing environment and is never touched by any step above.

## Done when

- [x] `helm template | kubeconform` clean for both `staging` and `prod` overlays
- [x] `kaval-staging` and `kaval-prod` both deploy and report `postgres.ok: true`
- [x] The running `imageID` for the promoted component matches byte-for-byte between staging
      and prod
- [x] A deliberate failure in prod is rolled back, with the restore time measured and recorded
- [x] Both rehearsal releases are torn down afterward; `kaval-local` is unaffected throughout

---

## What actually happened, live (2026-10-02)

Commit `8e29121`. All four images built once, tagged `sha-8e29121`, imported once.

**Staging and prod both came up clean** — 5/5 and 6/6 pods `Running` respectively (`prod`'s
`gateway.replicas: 2` from `prod/values.yaml` — both ready, a real effect of a values-only
difference). The promoted gateway's `imageID` matched byte-for-byte between the two releases:
`sha256:6befcf85cc34…` in both.

**The first break attempt found a more interesting bug than the one it was looking for.**
Setting `gateway.image.tag` to a bad value didn't produce a broken rollout to roll back from —
it failed the whole `helm upgrade` outright, because `migrate-job.yaml`'s pre-upgrade hook runs
from `gateway.image` too, and it couldn't pull the bad tag either. Helm refused the release
before touching a single running pod. Recorded in [ADR-0023](../adr/0023-promotion-rehearsal-on-k3d.md)
as a real, useful finding: **a bad gateway image can never reach a running `kaval-prod` pod,
because the migration hook gates it first.**

**The real drill used `executor.image.tag` instead**, which nothing pre-upgrade depends on.
The broken pod sat in `ErrImagePull`; the old, healthy executor pod was never torn down,
because Kubernetes' own rolling-update default (`maxUnavailable` rounds to 0 for a
single-replica Deployment) keeps at least one good replica running throughout. The service was
never actually down — only the release was left in a mixed state.

`helm rollback kaval-prod 3 -n kaval-prod --wait`, timed: **1.49 seconds.** Fast because there
was almost nothing to restore — the good ReplicaSet was already there and already healthy;
rollback's job was releasing the broken one, not resurrecting anything. The honest reading:
for a single-replica Deployment, Kubernetes' default rollout strategy is the real safety net,
and `helm rollback` is release-bookkeeping cleanup after the fact, not a service recovery in
this particular case. A `Recreate`-strategy or all-replicas-at-once failure would measure a
genuinely different, slower number — not this one.

Both rehearsal releases uninstalled afterward. Node memory: 58% with three releases running,
back to 40% with just `kaval-local`.
