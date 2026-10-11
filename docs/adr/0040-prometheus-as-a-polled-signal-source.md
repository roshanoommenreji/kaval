# ADR-0040 — Prometheus as a polled signal source

- **Status:** Accepted — built, deployed to the local k3d cluster, verified live
- **Date:** 2026-10-11
- **Deciders:** Roshan
- **Story:** `KAV-75` · closes Phase 3 · follows [ADR-0022](0022-real-kubernetes-events-as-signals.md), which deferred it

## Context

Kubernetes events say a container *has* been killed. Prometheus can say one is *about to be*: it
keeps a running measurement of every container's memory, and "95% of its limit" is a warning
before the kill. [ADR-0022](0022-real-kubernetes-events-as-signals.md) deferred this on purpose,
so the first real source (events) was not bundled with the second. The only reason recorded for the
deferral is scope. The 4 GB node budget ([overview](../architecture/overview.md)) allowed ~400 MB
for Prometheus, but that was an estimate nobody had measured.

## Decision

### Poll Prometheus; do not wait for it to push

`kaval_collector.prometheus` asks Prometheus a question every 30 seconds
(`GET /api/v1/query`) and writes `signal` rows. The architecture already said "Prometheus is
polled; Alertmanager pushes", and this is the polled half. Alertmanager (the pushing half) stays
a separate, later source.

What real teams do: install the kube-prometheus-stack bundle (Prometheus, Alertmanager, Grafana,
exporters, an operator; roughly 1 GB or more) and let Alertmanager push firing alerts to a
webhook. That is the right shape for a team with a fleet and an on-call rota. It is the wrong size
for a 4 GB node that also runs a language model, so this builds the smaller version: one
Prometheus and one poller. The poller's rule can later be replaced by an Alertmanager webhook
without touching anything downstream, because both write the same signal rows.

### One rule: a container near its memory limit

Every pass runs two plain queries, `container_memory_working_set_bytes` and
`container_spec_memory_limit_bytes`, for the demo namespace, and works out the ratio in Python.
A container at or above 90% gets a `container_memory_near_limit` signal. Doing the division in
Python instead of in PromQL is deliberate: a limit of 0 means "no limit", which PromQL turns into
an infinite ratio, and a threshold test written in Python is a unit test rather than a live
experiment. A container with no limit is skipped, because "near its limit" cannot be said of it.

### The value shape is the one already in use

The signal value is `{metric, container, value_bytes, limit_bytes}`, exactly what
`kaval_collector.synthetic` and the 20 golden incidents already use, and `source = "prometheus"`.
`kaval_agent.correlate` already maps that kind to the cause `memory_pressure`, so the agent needed
no change. Proven live: the incident opened by itself.

### A level, not an event: renew while it persists

An event has a `count`; a container sitting at 95% does not. So the de-duplication differs from
ADR-0022's. The poller writes one signal per container, then another every `--renew` seconds (300)
while it stays above the line. Writing only the first would repeat the trap ADR-0022 names:
`correlate` closes an incident after 15 quiet minutes, so a single signal would close the
incident while the container is still at its limit. Writing one per poll would flood the table.

### In the chart, switched off by default

`deploy/charts/kaval/templates/prometheus.yaml`, guarded by `prometheus.enabled`. Environments
differ only by their values file, so only `deploy/environments/local` turns it on; staging and
prod stay off until it has been measured on a real node.

Two Deployments, not one:

- **prometheus** scrapes each node's kubelet container measurements and drops everything except
  the two metrics the rule uses before storing them (that, plus a 2-hour retention, is what keeps
  it small). It needs cluster-wide read on `nodes` and `nodes/metrics`, because nodes belong to no
  namespace. That is the first ClusterRole in this chart. It is read-only, with no pods, secrets or
  write verbs. Storage is an `emptyDir`: measurements are disposable, the database is the record.
- **collector-prometheus** is the poller. It never calls the Kubernetes API, so it has no token
  mounted at all (`automountServiceAccountToken: false`). Same structural guarantee as the events
  collector, with less to give.

### The standard library, not a new dependency

The poller uses `urllib` rather than `httpx`. The collector image deliberately carries only the
Kubernetes client; `httpx` lives in the gateway and agent extras. One GET request is not worth a
dependency, a lock-file change and a bigger image.

### The kubelet's certificate is verified

Prometheus reaches the kubelet over HTTPS. Many setups set `insecure_skip_verify: true` to get past
a certificate that does not cover the address; here that is the first thing tried and found
unnecessary on k3d, so verification is on (`prometheus.kubeletInsecureSkipVerify: false`). It is a
values switch, so an environment whose kubelet certificate does not cover the node address can
turn it off in that one file without a template change.

## Measured, not estimated

On the k3d cluster on the dev server (one node, 96 stored series, about 20 minutes of data, 2-hour
retention, 30-second scrapes):

| | Memory |
|---|---|
| Prometheus | **33 MiB** (27 MiB at first) |
| collector-prometheus (a Python process) | 45 MiB |
| **Added in total** | **~78 MiB**, against ~400 MB budgeted for Prometheus alone |

The caveat that matters: this is one small cluster for a short time. Prometheus's memory grows
with the number of series and the retention window, and the prod node has more containers than the
demo. It is far from the budget, but the honest status is "measured small on k3d, to be re-measured
on staging before prod turns it on".

## Consequences

**Easier.** A container creeping toward its memory limit produces a real signal and a
`memory_pressure` incident before it is killed. A second real source now exists, and Phase 5's
chaos work has something to inject against besides crashing pods. Adding another Prometheus rule
is one more query and a threshold.

**Harder.** Two more pods per environment that turns this on. A Prometheus that is down is a blind
spot, so the poller says "prometheus unreachable" loudly on every pass and keeps retrying (it
resumed on its own in the live test); nothing yet raises that as an incident of its own.

**Accepted gaps, stated.**
- One rule only (memory near limit). CPU throttling, restart counts and disk are the same shape
  and a few lines each, left until something needs them.
- Only the demo namespace is watched, the same scope line ADR-0022 drew.
- Staging and prod do not run Prometheus yet.
- A pod that is killed before Prometheus has scraped it near its limit is invisible to this rule.
  That is what the Kubernetes events source is for.
- A local cluster created before the 2026-10-06 database-server change cannot upgrade with the
  new chart until its stored Postgres secret gains `POSTGRES_HOST` and `POSTGRES_PORT` (found live,
  Lab 43). A fresh cluster is unaffected.

**Revisit if:** Alertmanager becomes a source (then decide whether this rule moves into an alert
rule), more than one namespace needs watching, or the prod node's measured headroom changes.

## Verification

- `make lint` and the collector tests green against a real Postgres, including the
  renew-but-not-every-pass behaviour, the 0-limit case and the threshold edge.
- `helm lint` clean for all three environments. Staging and prod still render 13 resources; local
  renders 24 (17 + 7 for Prometheus).
- Live on k3d (Lab 43): the permission checks returned `yes, yes, yes, no, no, no, no` (read nodes
  and node metrics; not pods, secrets, delete, or create); the scrape target came up; a container
  held at 88% of its limit wrote nothing and at 94% wrote a signal; the agent opened
  `memory_pressure:k8s:kaval-demo/memhog` on its own; with Prometheus scaled to zero the poller
  logged the failure and survived; when it came back the poller resumed and renewed.
