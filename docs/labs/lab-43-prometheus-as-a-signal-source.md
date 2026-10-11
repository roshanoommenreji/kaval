# Lab 43 — Prometheus as a signal source

**Phase:** 3 (closes it) · **Time:** ~60 min · **Cost:** about $0.10 (the dev server for the session; nothing recurring)

Until now Kaval learned about a problem only after it had happened: a pod was killed, Kubernetes
wrote an event, the collector copied it down ([Lab 17](lab-17-real-k8s-events.md)). This lab adds a
warning *before* the kill. A small Prometheus keeps a running measurement of every container's
memory, and a second collector asks it which containers are above 90% of their limit. The design
is [ADR-0040](../adr/0040-prometheus-as-a-polled-signal-source.md).

## Prerequisites

- [Lab 17](lab-17-real-k8s-events.md) done: the k3d cluster `kaval-local` with the chart installed
- `AWS_PROFILE=kaval make devbox-up` finished and printed `ready`

---

## Step 1 — the unit tests, against a real database

The poller's logic (the 90% line, "no limit" is skipped, renew but not on every pass) is tested
against a throwaway Postgres, so the database tests actually run instead of being skipped:

```bash
docker run -d --rm --name kaval-test-pg -e POSTGRES_PASSWORD=t -e POSTGRES_USER=kaval \
  -e POSTGRES_DB=kaval -p 55432:5432 pgvector/pgvector:pg16
export POSTGRES_USER=kaval POSTGRES_PASSWORD=t POSTGRES_HOST=localhost POSTGRES_PORT=55432 \
  POSTGRES_DB=kaval POSTGRES_SSLMODE=disable
alembic upgrade head
KAVAL_REQUIRE_DB=1 pytest services/collector -q      # 35 passed
docker stop kaval-test-pg
```

## Step 2 — render the chart before deploying anything

```bash
for e in local staging prod; do
  helm lint deploy/charts/kaval -f deploy/environments/$e/values.yaml
  helm template kaval-$e deploy/charts/kaval -f deploy/environments/$e/values.yaml | grep -c '^kind:'
done
```

Expect `24` for local and `13` for staging and prod. The switch is off by default, so an
environment that does not opt in renders exactly what it did before.

## Step 3 — build, import, upgrade

`collector` is not a service name in `compose.yaml`; the image is built by the `signals` service:

```bash
docker --context kaval-devbox compose --profile tools build signals
tar cf - deploy/charts deploy/environments/local | ssh kaval-devbox 'cd ~/kaval-deploy && tar xf -'
ssh kaval-devbox 'k3d image import kaval/collector:dev -c kaval-local &&
  cd ~/kaval-deploy && helm upgrade kaval-local deploy/charts/kaval \
  -f deploy/environments/local/values.yaml --wait --timeout 300s'
```

> **Found live: an old cluster cannot take this upgrade as it is.** The migration job waited
> forever with `waiting for postgres... : - no response`, the empty host name before the colon
> being the clue. The cluster's `kaval-local-postgres` Secret dated from before the 2026-10-06
> database-server change (KAV-32) and held only three keys; the newer chart expects the host and
> port inside the Secret too. The migration runs *before* the Secret is refreshed, so it never
> could. The fix on a throwaway cluster:
>
> ```bash
> kubectl patch secret kaval-local-postgres \
>   -p '{"stringData":{"POSTGRES_HOST":"kaval-local-postgres","POSTGRES_PORT":"5432"}}'
> kubectl delete job kaval-local-migrate-6
> ```
>
> and run the upgrade again. A fresh `k3d` cluster has no such problem. It is recorded here rather
> than fixed in the chart because the chart is right; the cluster was stale.

## Step 4 — prove what Prometheus is allowed to do

```bash
SA=system:serviceaccount:default:kaval-local-prometheus
for q in "get nodes" "list nodes" "get nodes/metrics" "get pods" \
         "get secrets -n kaval-demo" "delete pods -n kaval-demo" "create nodes"; do
  echo -n "$q -> "; kubectl auth can-i $q --as=$SA
done
```

Result, live: `yes, yes, yes, no, no, no, no`. It can read the nodes and their metrics, and
nothing else. The poller has no permission to prove because it has no Kubernetes access at all:
no ServiceAccount token is mounted into it.

## Step 5 — is it scraping?

```bash
kubectl port-forward svc/kaval-local-prometheus 9090:9090 &
curl -s localhost:9090/api/v1/targets
```

Expect one target, `https://<node>:10250/metrics/cadvisor`, health `up`. It came up first try, and
it still did after switching to **verified** TLS (`kubeletInsecureSkipVerify: false`), so that
became the default instead of the usual shortcut.

## Step 6 — fill a container on purpose

A pod with a 128 MiB limit, holding about 116 MiB:

```bash
cat <<'EOF' | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata: {name: memhog, namespace: kaval-demo}
spec:
  restartPolicy: Never
  containers:
    - name: memhog
      image: kaval/collector:dev
      imagePullPolicy: Never
      command: ["python","-c","import time; x=b'a'*(116*1024*1024); print('holding',flush=True); time.sleep(7200)"]
      resources:
        limits: {memory: 128Mi}
EOF
```

`b'a' * N` is used, rather than `bytearray(N)`, because the latter is not written to and the
memory would never count. A first attempt with 108 MiB landed at **88%** of the limit, under the
line, and the poller correctly wrote nothing (`nothing new`). That accident is a useful half of the
proof. With 116 MiB:

```bash
kubectl logs deploy/kaval-local-collector-prometheus -c collector-prometheus -f
```

```
container_memory_near_limit  kaval-demo/memhog  memhog 94% of limit
```

## Step 7 — the rest of the chain, unattended

About a minute later (the agent groups signals for 60 seconds before opening anything), the agent's
own log:

```
waiting   1 signals: a group opens 60 s after its first signal
opened    memory_pressure:k8s:kaval-demo/memhog  low  1 signals
```

The row in the database is `{"metric": "container_memory_working_set_bytes", "container":
"memhog", "value_bytes": 125497344, "limit_bytes": 134217728}`, the same shape the synthetic
scenarios and the golden evals use, which is why the agent needed no change. No row was written
by hand.

## Step 8 — what happens when Prometheus is down

```bash
kubectl scale deploy/kaval-local-prometheus --replicas=0      # wait ~70 s, read the poller's log
kubectl scale deploy/kaval-local-prometheus --replicas=1      # wait ~75 s
```

Down: `prometheus unreachable: http://kaval-local-prometheus:9090: <urlopen error [Errno 111]
Connection refused>` on every pass, and the pod did not crash. Back up: the poller resumed on its
own and wrote a fresh `container_memory_near_limit` for `memhog`, which is the renew behaviour
(more than 5 minutes had passed since the first one).

## Step 9 — measure, then clean up

```bash
kubectl top pod | grep -E "prometheus|NAME"
kubectl delete pod memhog -n kaval-demo
```

| | Memory |
|---|---|
| Prometheus | 33 MiB (27 MiB at the first reading) |
| collector-prometheus | 45 MiB |

96 series stored, one node, 2-hour retention. This replaces the guess of ~400 MB in
`docs/architecture/overview.md`. It is one small cluster for a short time, so staging gets the real
re-measurement before prod turns it on.

## Done when

- [x] `pytest services/collector` passes against a real Postgres (35 passed)
- [x] Local renders 24 resources; staging and prod still render 13
- [x] `kubectl auth can-i` returns `yes, yes, yes, no, no, no, no` for the Prometheus account
- [x] The scrape target is `up` with certificate verification on
- [x] A container at 88% writes nothing; at 94% it writes a signal, with no hand-written row
- [x] The agent opens `memory_pressure:k8s:kaval-demo/memhog` on its own
- [x] A Prometheus that is down is reported on every pass and the poller survives
- [x] Prometheus's own memory is measured
- [x] `AWS_PROFILE=kaval make devbox-down` (or let it stop itself after an idle hour)
