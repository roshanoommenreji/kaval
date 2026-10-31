# Lab 67 — Two health pages and pods that pass `restricted`: built and proven locally

**Phase:** 7 follow-ups · **Stories:** `KAV-106` and `KAV-105`, one package · **Time:** about 2 hours · **Cost:** $0 for this lab (the staging run that comes next is about $1)

[Lab 66](lab-66-the-eks-lab-live-two-sessions-and-what-they-found.md) found two weaknesses in the shared chart. This lab fixes both, test first, and proves what can be proven without AWS. The decisions are in [ADR-0068](../adr/0068-two-health-pages-and-pods-that-pass-restricted.md).

## Prerequisites

- The repository on a short branch, the project's `.venv` on the path, `helm`
- Docker running (Part 3), and any local Kubernetes cluster of version 1.25 or later for Part 4 (Docker Desktop's built-in one was used)

## Part 1 — Tests first, and watch them fail

1. `services/gateway/tests/test_gateway_health.py` gains five tests: `/livez` answers 200 with the database and the network both broken; `/readyz` answers 200 when only the model is down; `/readyz` answers 503 (and leaks no connection string) when the database is unreachable; `/healthz` still reports everything; both pages are in the API description.
2. `scripts/dev/test_chart_security.py` renders the chart for staging, prod, local and the lab and checks every pod and init container against the `restricted` list, that the in-cluster database has its own user, and that the gateway's checks use the new pages. A negative test hands the checker a loose pod and a pod running as root and expects it to report both.
3. Run `python -m pytest scripts/dev/test_chart_security.py services/gateway/tests/test_gateway_health.py -q`. Before any change: **13 failed, 9 passed**, the failures being exactly the missing pages and the missing settings.

## Part 2 — The change

- **The gateway** (`services/gateway/kaval_gateway/main.py`): `/livez` returns the version and depends on nothing; `/readyz` runs the database check only and answers 503 if it fails; `/healthz` is untouched.
- **The chart:** a helper `kaval.podSecurity` (non-root user 10001, the runtime's default system-call filter) is included in every pod; every container and init container gets `allowPrivilegeEscalation: false` and all capabilities dropped. Prometheus keeps its own user (65534) and gains the filter; the in-cluster Postgres runs as 999 with `fsGroup: 999` and `PGDATA` in a subfolder. The gateway's probes become `httpGet` on `/livez` (liveness) and `/readyz` (readiness).
- Re-run the tests: **22 passed** (11 chart-security, 11 gateway-health). The whole suite and `helm lint` for all four environments are clean.

## Part 3 — A real database under the new settings

The one pod where a test file cannot tell you the truth is the database: it must be able to start as a non-root user on a disk it does not own. Kubernetes' `fsGroup` gives a fresh disk's top folder to group 999 with group write, root as owner. Docker can imitate that with a memory-backed folder (an ordinary Docker volume copies the image's own ownership and hides the problem; this was learned by getting it wrong first):

```bash
export MSYS_NO_PATHCONV=1      # Git Bash rewrites /var/... paths otherwise
T='/var/lib/postgresql/data:uid=0,gid=999,mode=2775'
COMMON="--user 999:999 --cap-drop ALL --security-opt no-new-privileges \
  -e POSTGRES_PASSWORD=x -e POSTGRES_USER=kaval -e POSTGRES_DB=kaval"
docker run -d --name pg-new $COMMON -e PGDATA=/var/lib/postgresql/data/pgdata --tmpfs $T pgvector/pgvector:pg16
docker run -d --name pg-old $COMMON --tmpfs $T pgvector/pgvector:pg16
```

| Layout | Result |
|---|---|
| New: data in a subfolder (`PGDATA=.../pgdata`) | Accepting connections as `postgres` (uid 999); the `vector` extension is available; still accepting connections after a restart |
| Old: data in the disk's top folder, same user, same disk | `initdb: error: could not change permissions of directory ... Operation not permitted`, container exits |

So the subfolder is not decoration: without it, a non-root database cannot initialise on a Kubernetes disk. Remove both containers afterwards.

## Part 4 — Kubernetes' own checker on the real chart

A server-side dry run sends the rendered chart to a real cluster without creating anything, and a namespace labelled `pod-security.kubernetes.io/warn=restricted` makes the cluster say what would be refused.

```bash
kubectl create ns psa-test
kubectl label ns psa-test pod-security.kubernetes.io/warn=restricted pod-security.kubernetes.io/audit=restricted
helm template kaval-local deploy/charts/kaval -n psa-test -f deploy/environments/local/values.yaml \
  | kubectl apply -n psa-test --dry-run=server -f - 2>&1 | grep -c "would violate"
kubectl delete ns psa-test
```

| Chart | Pods the cluster said "would violate `restricted`" |
|---|---|
| Before (`main`), local values | **9** (every Deployment and CronJob: no privilege-escalation setting, no dropped capabilities, ...) |
| After, local values | **0** (33 objects accepted) |
| After, prod values | **0** (33 objects accepted) |
| After, staging values | **0** (41 objects accepted) |

Not done: a minikube profile would not start on this laptop (the cluster's own control plane stayed stopped), so Docker Desktop's cluster did the dry run. A dry run proves the cluster would admit the pods; it does not run them. Running them is the staging smoke test.

## Part 5 — The first staging run found a fourth thing

`ASSUME_YES=1 make staging-up` built everything (91 resources) and then waited 15 minutes without the pods coming up. `make staging-status` showed one pod, the database migration, stuck in its first step. Its log said `10.61.1.71:5432 - no attempt` over and over, and `no attempt` is different from `no response`: the check had not even tried to connect.

Reproduced locally with no AWS (about a minute):

```bash
export MSYS_NO_PATHCONV=1
docker network create pgt
docker run -d --rm --name pgt-db --network pgt -e POSTGRES_PASSWORD=x -e POSTGRES_USER=kaval pgvector/pgvector:pg16
sleep 8
# the old wait, as the chart's new user 10001:
docker run --rm --network pgt --user 10001:10001 -e POSTGRES_HOST=pgt-db -e POSTGRES_PORT=5432 -e POSTGRES_USER=kaval \
  pgvector/pgvector:pg16 sh -c 'pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT"'
# the fixed wait:
docker run --rm --network pgt --user 10001:10001 --cap-drop ALL --security-opt no-new-privileges -e POSTGRES_HOST=pgt-db -e POSTGRES_PORT=5432 -e POSTGRES_USER=kaval \
  pgvector/pgvector:pg16 sh -c 'pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER"'
docker rm -f pgt-db; docker network rm pgt
```

| Wait command, run as user 10001 | Result |
|---|---|
| `pg_isready -h … -p …` (what the chart had) | `no attempt` — the image has no account for user 10001, so the tool cannot pick a default database user and gives up before connecting |
| `pg_isready -h … -p … -U "$POSTGRES_USER"` | `accepting connections` |

The fix is the same one word in seven places (agent, collector, the Prometheus poller, cost, diagnoser, executor, migrate). `pg_dump` in the backup job already named its user and was re-checked as 10001: it connects. A chart test now fails if any `pg_isready` in any environment omits `-U` (red first on all four environments, then green), with a negative case.

Why nothing earlier caught it: the chart tests read the rendered file and the cluster's dry run asks "would you admit this pod?". Neither runs the pod. This is the gap the staging run exists to close.

## Part 6 — What staging proves next (not done yet)

`make staging-up`, then `make staging-smoke`. Its "pods running and ready" check is the live proof of both stories: a gateway image without `/readyz` would never become ready, and a pod whose new settings stop it starting would not either. Production then picks the images up at its next bring-up through the normal promotion gate, followed by `make prod-smoke`. The EKS lab is not run again; when it next runs, the namespace can enforce `restricted` and the Ingress health path can move to `/readyz`.

## Done when

- Tests: the 22 health and chart-security tests pass (with the neighbouring chart and lab tests, 56); the whole suite, `helm lint` (four environments), ruff and mypy on the changed code are clean
- The database starts under the new settings and fails under the old layout (Part 3)
- The cluster's checker flags the old chart 9 times and the new chart 0 times (Part 4)
- ADR-0068, the roadmap lines, the runbook, the repo guide, the journal are updated
- Still open: the staging run, promotion, prod bring-up and `make prod-smoke` (`KAV-105`, `KAV-106` stay open until then)

## Cost

Nothing: no AWS resource was created. The staging run is about $1 and is shared with whatever else is waiting to be proven there.
