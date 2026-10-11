# Lab 44 — Recording whether a fix held, and reading the time to restore

**Phase:** 5, slice 1 · **Time:** ~75 min · **Cost:** about $0.10 (the dev server for the session; nothing recurring)

Until now Kaval could restart a pod and then never learn whether that helped. This lab adds the
missing last step: five minutes after a fix, the executor checks whether the same workload is
still being reported as broken, and writes an `outcome` row. `make mttr` then shows how long the
whole thing took, stage by stage. The design is
[ADR-0041](../adr/0041-service-levels-and-recording-whether-a-fix-held.md); the targets are in
[service levels](../architecture/service-levels.md).

## Prerequisites

- [Lab 43](lab-43-prometheus-as-a-signal-source.md) done: k3d cluster `kaval-local` with Prometheus on
- `AWS_PROFILE=kaval make devbox-up` finished and printed `ready`

---

## Step 1 — the tests, against a real database

```bash
docker run -d --rm --name kaval-test-pg -e POSTGRES_PASSWORD=t -e POSTGRES_USER=kaval \
  -e POSTGRES_DB=kaval -p 55432:5432 pgvector/pgvector:pg16
export POSTGRES_USER=kaval POSTGRES_PASSWORD=t POSTGRES_HOST=localhost POSTGRES_PORT=55432 \
  POSTGRES_DB=kaval POSTGRES_SSLMODE=disable KAVAL_REQUIRE_DB=1
alembic upgrade head
pytest services/executor -q          # 45 passed
docker stop kaval-test-pg
```

`services/executor/tests/test_verify.py` covers: a fix that held; one that did not (the negative
case); nothing judged before five minutes; judged once, not every pass; a neighbour's trouble
and late noise not counted against the fix; a failed execution and a non-Kubernetes incident
left alone; a regression flagged once; and the stage numbers adding up to the stored total.

## Step 2 — build the executor and put it in the cluster

```bash
docker --context kaval-devbox compose build executor
ssh kaval-devbox 'docker save kaval/executor:dev | \
  docker exec -i k3d-kaval-local-server-0 ctr -n k8s.io images import -'
ssh kaval-devbox 'kubectl delete pod -l app.kubernetes.io/component=executor'
```

> **Found live: `k3d image import` silently kept the old image.** The second import of the same
> tag printed `Successfully imported` in one second and changed nothing; the pod kept running
> the previous code. The giveaway was a report with the old column values. Importing straight
> into containerd with `ctr ... images import -` replaced it. When a rebuilt `:dev` image seems
> to be ignored, compare `docker images` on the server with `crictl images` inside the k3d node.

## Step 3 — make two real problems

Two workloads that hold 116 MiB under a 128 MiB limit (94%, over the 90% line from Lab 43). One
is a bare pod, so deleting it ends the problem. The other is a Deployment, so its replacement
fills up again.

```bash
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: Pod
metadata: {name: hoga, namespace: kaval-demo}
spec:
  restartPolicy: Never
  containers:
    - {name: hog, image: kaval/collector:dev, imagePullPolicy: Never,
       command: ["python","-c","import time; x=b'a'*(116*1024*1024); time.sleep(7200)"],
       resources: {limits: {memory: 128Mi}}}
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: hogb, namespace: kaval-demo}
spec:
  replicas: 1
  selector: {matchLabels: {app: hogb}}
  template:
    metadata: {labels: {app: hogb}}
    spec:
      containers:
        - {name: hog, image: kaval/collector:dev, imagePullPolicy: Never,
           command: ["python","-c","import time; x=b'a'*(116*1024*1024); time.sleep(7200)"],
           resources: {limits: {memory: 128Mi}}}
EOF
```

About two and a half minutes later the agent opened both incidents on its own
(`memory_pressure:k8s:kaval-demo/hoga` and `.../hogb`), from real Prometheus signals.

## Step 4 — a proposal, an approval, a fix

The model call is proven in Labs 10–11, so the proposal and its one `restart_pod` action are
written by hand, as in [Lab 16](lab-16-executor-and-scoped-rbac.md) Step 7 (a short script run in the
agent pod). The approval goes through the real gateway:

```bash
kubectl exec deploy/kaval-local-agent -c agent -- python -c "
import urllib.request, json
r = urllib.request.Request('http://kaval-local-gateway:8000/v1/actions/<ACTION_ID>/decisions',
    data=json.dumps({'verdict':'approved','actor':'roshan','reason':'KAV-77 live proof'}).encode(),
    headers={'Content-Type':'application/json'}, method='POST')
print(urllib.request.urlopen(r).status)"
```

The executor acted within its 15-second loop: `deleted pod kaval-demo/hoga … owner=none` and
`deleted pod kaval-demo/hogb-… owner=ReplicaSet`. A pause of about a minute was left between
filing the proposals and approving them, so the human stage would show up in the numbers.

## Step 5 — wait five minutes, do nothing

```bash
kubectl logs deploy/kaval-local-executor -c executor -f
```

```
resolved     memory_pressure:k8s:kaval-demo/hoga  209s
NOT RESOLVED memory_pressure:k8s:kaval-demo/hogb
```

`hoga` had no controller, so the pod stayed gone and the signals stopped: the fix held.
`hogb`'s replacement filled its memory again within a minute and Prometheus reported
`hogb-…-9bwz2` at 94%: the restart did not fix it, and the row says so. This is the negative
proof from live data rather than a unit test: a restart is not a cure for a container that is
simply too small, which is exactly why Phase 5 adds a "raise the memory limit" fix (`KAV-79`).

The `outcome` table, read directly:

| incident | resolved | regression | mttr_sec |
|---|---|---|---|
| memory_pressure:k8s:kaval-demo/hoga | t | f | 209 |
| memory_pressure:k8s:kaval-demo/hogb | f | f | |

(Two older incidents from Labs 16 and 17 were judged too, as soon as the new executor started,
because their fixes were more than five minutes old. One has no seconds: it was written by hand
with no signals attached, so there is no first signal to measure from.)

## Step 6 — read the time to restore

```bash
make mttr          # on the laptop: python -m kaval_executor.mttr against the .env database
kubectl exec deploy/kaval-local-executor -c executor -- python -m kaval_executor.mttr
```

```
incident                                      detect diagnose   human   queue execute    wall machine
memory_pressure:k8s:kaval-demo/hoga              88s      51s     60s      1s     10s    210s    150s
crashloop:k8s:kaval-demo/crashy                  66s      87s    250s      1s      0s    405s    155s

2 incident(s)       median      p90    worst
machine             152s    155s    155s
wall-clock          307s    405s    405s
```

Read it left to right: 88 s from the first signal to the incident being written (the correlator
waits 60 s to group signals, plus the 30 s poll), 51 s until a proposal existed (here, my typing),
60 s for a person, 1 s for the executor to notice, 10 s to act. The stages add up to the wall-clock
figure exactly, and `machine` is that figure minus the human stage.

> **Found live: the first version printed `detect 0s`.** It measured from `incident.opened_at`,
> which is the first *signal's* time, not when the row was written. The moment of detection is the
> row's `created_at`. The unit tests had passed because their fixtures set both to the same value;
> the live run is what showed it. Both the code and the test fixtures now use `created_at`.

## Step 7 — clean up

```bash
kubectl delete deploy hogb -n kaval-demo; kubectl delete pod hoga -n kaval-demo
AWS_PROFILE=kaval make devbox-down        # or let it stop itself after an idle hour
```

## Done when

- [x] `pytest services/executor` passes against a real Postgres (45 passed)
- [x] A fix that held is written as `resolved` with a wall-clock time to restore (`hoga`, 209 s)
- [x] A fix that did not hold is written as not resolved and has no time to restore (`hogb`)
- [x] Nothing is judged before five minutes, and nothing is judged twice
- [x] `make mttr` shows stages that add up, machine time and wall-clock apart, median/p90/worst
- [x] No hand-written outcome row; the verifier ran inside the deployed executor
- [x] The database grants (`db-roles.sql`) and the verifier proven on **staging** with the role-separated database: `resolved memory_pressure:k8s:kaval-demo-staging/hoga 152s`, `make mttr` 152 s wall / 151 s machine (2026-10-11, [Lab 45](lab-45-watching-the-watcher.md))
