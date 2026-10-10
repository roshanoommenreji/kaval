# Lab 38 — Moving to the Kubernetes client 37, and proving the executor on staging

**Phase:** 4 · **Story:** `KAV-70` · **Cost:** about $0.30 (staging up for roughly 25 minutes at ~$0.045/hr).
Decisions: [ADR-0010](../adr/0010-ci-pipeline-and-supply-chain.md) (amendment, "Closed 2026-10-10").

The `kubernetes` Python library is what the executor uses to delete pods and what the collector uses to
read events. Version 37 is a major release that ships type hints. Dependabot was told to skip it
(`KAV-69`) until the executor, the one component that changes the cluster, had been run against a real
cluster with it. This lab does both halves: make the type checker happy, then prove it live.

**Result:** library 36.0.3 → 37.0.1 in `uv.lock`; `make lint` clean (66 files); 143 service tests pass;
on staging the executor found, recorded and deleted a pod, its controller replaced it, and a missing pod
came back as "not found". The Dependabot `ignore:` block is gone.

## 1. See what breaks

```bash
uv lock --upgrade-package kubernetes     # 36.0.3 -> 37.0.1
uv sync --frozen --all-extras
mypy services/                           # 20 errors in 3 files (the first report said 26: a different setup)
```

## 2. Fix it without changing behaviour

- `services/executor/kaval_executor/k8s.py`: the library now types `pod.metadata`, `.status` and `.spec` as
  optional. `restart_pod` falls back to an empty object for each (a real pod always has all three, so nothing
  changes at runtime) and counts restarts as `int(c.restart_count or 0)`.
- `services/collector/kaval_collector/k8s_events.py`: `poll()` used to ask for the whole `client.CoreV1Api`,
  which the test's small fake is not. It now asks for an `EventSource` protocol (one method,
  `list_namespaced_event`), so the real client and the fake both fit.
- Both `k8s.py` config loaders: `# type: ignore[no-untyped-call]` on the library's two untyped functions.

`make lint` and `pytest services` must pass before the pull request.

## 3. Prove it on staging

Merge the pull request; `release.yml` builds the images and opens "point staging at sha-X"; merge that.

```bash
git pull
AWS_PROFILE=kaval make staging-up        # ~5 min (run it yourself: the harness will not let Claude start AWS)
AWS_PROFILE=kaval make staging-smoke     # 7 checks; a pass is recorded in deploy/promotion/passed-staging.json
```

Then exercise the executor inside its own pod, over Session Manager on the staging node. Note the executor's
role exists only in the **demo** namespace (`kaval-demo-staging`), never in its own; asking it about a pod in
`kaval-staging` is correctly refused with a 403 (first attempt, and the proof the boundary holds).

```bash
kubectl create deployment kaval-probe -n kaval-demo-staging --image=busybox:1.36 -- sleep 3600
kubectl exec -n kaval-staging deploy/kaval-staging-executor -c executor -- python -c \
  "from kaval_executor.k8s import load_config, restart_pod; load_config(); \
   print(restart_pod('kaval-demo-staging/<pod>')); print(restart_pod('kaval-demo-staging/does-not-exist'))"
kubectl delete deployment kaval-probe -n kaval-demo-staging
```

Observed on `sha-9b2eb7d` with `kubernetes 37.0.1`: `found: True, owner_kind: ReplicaSet`, then
`deleted: True, replacement: expected from its controller`; a second pod appeared within seconds; the missing
pod gave `found: False` / "not found; nothing to restart".

## 4. Release the hold

Delete the `ignore:` block under the `uv` entry in `.github/dependabot.yml`. Then `make staging-down`.

## Not covered

The full chain (agent proposal → approval → executor loop → `execution` row) was not run: this exercised
the library calls the executor makes, which is all that changed. Prod promotion goes through `promote.yml`
like any other release.
