# Lab 45 — Watching the watcher: Kaval tells you when Kaval stops

**Phase:** 5, slice 2 · **Time:** ~2 h (local ~45 min, staging ~75 min) · **Cost:** about $0.15 (staging for the session, plus the dev server; nothing recurring)

Until now nothing would notice if the collector, agent or executor froze. This lab gives every Kaval
service a small health page, has Prometheus judge those pages with three rules, turns a firing rule
into a signal and then a Slack message, and proves each step. The design is
[ADR-0042](../adr/0042-watching-the-watcher.md); why there is no Alertmanager is
[ADR-0043](../adr/0043-alertmanager-not-adopted-yet.md); what to do when the message arrives is
[the runbook](../runbooks/kaval-stopped-reporting.md).

## Prerequisites

- [Lab 43](lab-43-prometheus-as-a-signal-source.md) (Prometheus on the local cluster) and
  [Lab 44](lab-44-recording-whether-a-fix-held.md) done
- `AWS_PROFILE=kaval make devbox-up` finished; Docker running on the laptop

---

## Step 1 — the rules, tested before they ever run

```bash
make rules-test
```

`promtool test rules` feeds the rules made-up numbers and checks what fires. The file
`deploy/charts/kaval/tests/kaval-alerts.test.yaml` has six cases, and the quiet ones matter as much:

| Case | Expected |
|---|---|
| A healthy executor, a pass every 15 s for 8 minutes | **nothing fires** |
| A loop whose last pass was 60 s ago, then 3 minutes ago | nothing yet (under the 2-minute floor, then inside the 1-minute wait) |
| The same loop at 5 minutes | `KavalLoopStalled` |
| An agent with a 60 s interval, last pass 200 s ago | **nothing** (under 4 × 60 s) |
| A gateway whose `up` goes to 0 | `KavalTargetDown` after 2 minutes, not before |
| An executor with **no `up` series at all** | `KavalTargetDown` (this is the `absent()` case) |
| The watchdog | always fires |

## Step 2 — the code, against a real database

```bash
docker run -d --rm --name kaval-test-pg -e POSTGRES_PASSWORD=t -e POSTGRES_USER=kaval \
  -e POSTGRES_DB=kaval -p 55432:5432 pgvector/pgvector:pg16
export POSTGRES_USER=kaval POSTGRES_PASSWORD=t POSTGRES_HOST=localhost POSTGRES_PORT=55432 \
  POSTGRES_DB=kaval POSTGRES_SSLMODE=disable KAVAL_REQUIRE_DB=1
alembic upgrade head                 # includes the new notification table
pytest services -q                   # 264 passed
docker stop kaval-test-pg
```

New tests: the health page (what is on it and, by a test, what is not), the poller's alert path
(renewing, not repeating, the watchdog, "Prometheus unreachable only after three misses in a row"),
the Slack notifier (posts once, reminds after 30 minutes, never the watchdog, nothing recorded if Slack
fails), and the rule that a `kaval_*` incident can never get a proposal.

## Step 3 — put it on the local cluster

```bash
docker --context kaval-devbox compose build gateway signals agent executor
for img in gateway collector agent executor; do
  ssh kaval-devbox "docker save kaval/$img:dev | docker exec -i k3d-kaval-local-server-0 ctr -n k8s.io images import -"
done
tar cf - deploy/charts deploy/environments/local | ssh kaval-devbox 'cd ~/kaval-deploy && tar xf -'
ssh kaval-devbox 'cd ~/kaval-deploy && helm upgrade kaval-local deploy/charts/kaval \
  -f deploy/environments/local/values.yaml --wait --timeout 300s && kubectl rollout restart deploy -l app.kubernetes.io/part-of=kaval'
```

(`ctr ... images import -` rather than `k3d image import`, which silently keeps the old image: see Lab 44.)

Then ask Prometheus what it sees. From inside the cluster:

```
--- targets
kaval-agent                  up    http://10.42.0.203:9100/metrics
kaval-collector              up    http://10.42.0.205:9100/metrics
kaval-collector-prometheus   up    http://10.42.0.204:9100/metrics
kaval-executor               up    http://10.42.0.206:9100/metrics
kaval-gateway                up    http://10.42.0.207:9100/metrics
cadvisor / prometheus        up
--- alerts firing
KavalWatchdog none
--- seconds since each service last finished a pass
collector-prometheus 20 · agent 21 · collector 39 · executor 32
```

All five services are read by plain DNS (no new permission for Prometheus), only the watchdog fires,
and every loop was last seen within its own interval: **the quiet case, live.** The same page also
gives each service's real memory (resident, MiB): gateway 90, collector 98, executor 81, agent 62,
poller 62, Prometheus 89. These are the first measured figures for the 4 GB budget; staging's are in
Step 6.

## Step 4 — make Kaval stop, three ways *(run on the local cluster, then on staging)*

Each fault is a real one, not a switch inside the code. After each, Prometheus fires the alert, the
poller writes a signal, the correlator opens a `kaval_*` incident, and the gateway posts to Slack where
Slack is configured.

**A. Alive but doing nothing: a database lock.** Hold an exclusive lock on the table the executor reads
every pass. The executor's process and its health page stay up; its loop waits.

```bash
kubectl exec deploy/<release>-postgres -- sh -c 'psql -U $POSTGRES_USER -d $POSTGRES_DB \
  -c "BEGIN; LOCK TABLE action IN ACCESS EXCLUSIVE MODE; SELECT pg_sleep(600);"'
```

Expect: `KavalLoopStalled` for `executor` about 3 minutes later (the 2-minute floor plus the 1-minute wait),
and **only** for the executor. End the lock (cancel the command) and the alert clears by itself.

**B. A pod that is gone.** `kubectl scale deploy/<release>-collector --replicas=0`. Expect `KavalTargetDown`
for `collector` after 2 minutes: the pod does not exist, so there is no `up` series to be 0, which is
the `absent()` case from Step 1, now live. Scale it back to 1.

**C. Prometheus itself.** `kubectl scale deploy/<release>-prometheus --replicas=0`. Expect the poller to log
`prometheus unreachable` three times, then write `kaval_prometheus_unreachable`, then an incident
`kaval_prometheus_unreachable:k8s:<ns>/prometheus`. No rule could have raised this one; it is the
poller's own check.

**D. The refusal.** Ask for a fix for one of these incidents:

```bash
python -m kaval_agent.diagnose <incident-id>
# kaval_loop_stalled:k8s:... is Kaval's own health: it is for a person, so no diagnosis is asked
# for and no fix proposed (see docs/runbooks/kaval-stopped-reporting.md)
```

> **Status:** Steps 4 and 6 are the live proof this lab exists for; the checklist at the end shows which are done.

## Step 5 — the dashboard

[`deploy/dashboards/kaval-health.json`](../../deploy/dashboards/kaval-health.json) is a Grafana
dashboard kept in git. It imports cleanly into Grafana 11.4 (checked with a throwaway container: the
API accepted it and returned all ten panels). To look at an environment:

```bash
kubectl port-forward deploy/<release>-prometheus 9090:9090     # on the node, or over SSM
docker run --rm -p 3300:3000 -e GF_AUTH_ANONYMOUS_ENABLED=true -e GF_AUTH_ANONYMOUS_ORG_ROLE=Admin grafana/grafana-oss:11.4.0
# add a Prometheus data source at http://host.docker.internal:9090, then import the JSON
```

Nothing runs on the node for this; the viewer exists only while you look.

## Step 6 — staging *(after the pull request merges and the release pins staging)*

1. `AWS_PROFILE=kaval make staging-up`, then `make staging-smoke`.
2. Repeat Step 3's check inside the staging cluster: five `up` targets, only the watchdog firing, for
   30 minutes with a pod restart in the middle (the quiet case must stay quiet).
3. Read each service's real resident memory and Prometheus's own, `kubectl top node`, and **kubelet TLS**
   (`cadvisor` target `up` with `kubeletInsecureSkipVerify: false`, not yet confirmed on k3s). Record the numbers in
   the [memory budget](../architecture/overview.md#runtime-footprint).
4. Step 4's faults A to D.
5. KAV-77's check on staging: file a proposal for a bare `hog` pod in `kaval-demo-staging`, approve it, wait five
   minutes, `kubectl exec deploy/<release>-executor -- python -m kaval_executor.mttr`. A row proves the
   verifier and the new read grants on a database where the roles are real.
6. `make staging-down`.

## Done when

- [x] `make rules-test` passes, including the quiet cases and the missing-pod case
- [x] `pytest services` passes against a real Postgres (264 passed), `ruff` and `mypy` clean
- [x] Local cluster: five health pages read by Prometheus, only the watchdog firing, loops within their intervals
- [x] The dashboard imports into Grafana and shows ten panels
- [ ] Local or staging: each fault (A–C) raises its alert, signal and incident, and clears when the cause is removed
- [ ] Staging: 30 quiet minutes with no false alarm; real memory of every service and of Prometheus recorded
- [ ] Staging: kubelet certificate verified (`cadvisor` target up with verification on)
- [ ] Staging: KAV-77's verifier and the database grants proven (`make mttr` row)
- [ ] The Slack message seen in a real channel (production, with the production rollout, `KAV-83`)
