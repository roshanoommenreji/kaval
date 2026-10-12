# ADR-0047 — Incidents that are for people: a full disk and a slow service

- **Status:** Accepted — code built and tested; the two staging game days are recorded in the
  [post-mortems](../postmortems/README.md) once run
- **Date:** 2026-10-12
- **Deciders:** Roshan
- **Story:** `KAV-81` (Phase 5, slice 5) · roadmap lines "Chaos CronJobs: disk full, latency spike" and
  "disk and latency signals"

## Context

Slices 3 and 4 proved Kaval can fix a program killed for memory and a bad release. Two other
everyday failures have **no fix a program should apply**: a disk that is filling, and a service that
has become slow. A restart frees no disk and speeds nothing up; the real fixes (grow a volume, find
what is writing, find what is slow) are a person's judgement, and a confident wrong action here costs
more than a page. The right outcome is therefore different in kind: **Kaval notices, tells a person,
and proposes nothing.** That has to be built and then proven by breaking it on purpose.

Kaval already has one such class, its own health ([ADR-0042](0042-watching-the-watcher.md): a `kaval_`
cause is for a person). This ADR widens it.

## Decision 1 — a short list of causes that are for people, enforced in four places

`kaval_shared.health.PEOPLE_ONLY_CAUSES` = `disk_pressure` and `latency_high`, plus every cause starting
`kaval_`. For an incident with one of these causes:

| Where | What it does |
|---|---|
| The agent (`diagnose.write_proposal`) | Refuses to write a proposal at all, whoever calls it; the model is never asked |
| The policy (`policy/policy.rego`) | The incident's cause is now part of the policy input; any action attached to one of these is **`never`**, at any confidence, for any action type, and a promotion cannot override it |
| The executor (`execute_one`) | Passes the cause (read from the action's own incident) to the same policy again before acting, so a proposal that got into the table some other way is still refused and recorded |
| The gateway | Already refuses to let anyone approve or deny a `never` action (HTTP 409) |

The list lives in two languages (Python for the agent and gateway, Rego for the policy); a test fails
if they ever differ. Why the policy and not only the agent (decision approved 2026-10-12): the agent is
the layer that can be wrong, and the whole architecture (ADR-0006) is that a refusal does not depend on
the thing being refused.

## Decision 2 — disk: use what is already measured; no new agent

| What real companies do | Notes |
|---|---|
| node_exporter on every machine, alert on `predict_linear` (full within N hours) and a percentage | The standard; a new agent (about 15–20 MiB here) |
| A cloud agent (CloudWatch agent, Datadog) | Paid or another account-level component; a CloudWatch agent also needs IAM |
| Read what the container runtime already reports | Free, nothing new to patch |

**Measured on staging first** (2026-10-12, `make staging-measure-disk`): cAdvisor, which Prometheus
already scrapes, reports the root disk's used and total bytes (6.10 GB of 21.40 GB, 28.5 percent; `df`
said 29 percent), and the kubelet's summary agrees. So **no new agent**: Prometheus keeps two more
cAdvisor metrics (`container_fs_usage_bytes`, `container_fs_limit_bytes`) and the poller asks for the
used fraction of real block devices over 1 GB (not `/dev/shm`, `/run`, or the 10 MB boot partition).

- **The line:** 0.85 (chart default, so prod when Prometheus reaches it). Kubelet's own eviction starts
  near 85 percent, so Kaval should speak before the machine does.
- **A trend (`predict_linear`) is not built.** Prometheus keeps two hours (ADR-0040), too little to
  predict a slow fill, and a threshold is enough to prove the chain. A longer retention or a node
  agent is the upgrade when a real slow fill is a worry; recorded, not hidden.
- **Staging's line is 0.38**, on purpose: the game day cannot fill 11 GB of a 20 GB disk, so staging
  sets a lower number in its own values file (the one allowed difference between environments). The
  code path is identical; a chart test keeps prod at 0.85.
- **A broken query is not a full disk:** a value that is not a fraction between 0 and 1 (NaN, infinity,
  a percentage by mistake) is ignored, not paged on.
- **Limits stated:** this is the node's root disk only. The database server is another machine with its
  own disk (watched under `KAV-82`, database monitoring); with more than one node the signal target
  would need a node name.

## Decision 3 — slowness: a timed request inside the poller we already have

| What real companies do | Notes |
|---|---|
| Probe from outside with a blackbox exporter, alert on duration | Standard; another program to run and patch |
| Measure every real request in the app (histograms, p99) and alert on that | Best, but needs the app instrumented and real traffic |
| A synthetic request on a schedule that is timed | Cheapest honest check; what an on-call person does with `curl` |

**Chosen:** a synthetic request, `kaval_collector.probe`, as one more thing the existing Prometheus
poller does each pass (decision approved 2026-10-12: not a new program).

- **Slow** = an answer over 1 second, **or no answer in 5 seconds** (a request that never returns is
  the slowest there is, so a timeout is slow, not down); **three passes in a row** (about 90 s), so one
  busy second never pages anyone. A fast answer of any kind (even an error page) and a refused
  connection are **not** slow: "is it up" has its own checks.
- **Targets come from the deployment's settings only** (`prometheus.probe.targets`), `http`/`https`
  only, at most five, redirects not followed, the body capped and thrown away. Nothing in the database,
  a signal or a model answer can add an address, so nothing outside the deployment can make the
  collector fetch a chosen URL.
- Written once, renewed every 5 minutes while slow (the same reasoning as the memory signals), and the
  Slack alert says one fixed sentence per kind: no number or text from the signal is copied in.
- Prod probes nothing today (its list is empty and Prometheus is not there yet, `KAV-83`); choosing
  real targets there (Kaval's own gateway first) is part of that story.

## Decision 4 — the game days, and why they are safe

Two experiments in the runner ([ADR-0044](0044-chaos-with-our-own-jobs-and-a-bounded-memory-fix.md) unchanged:
switch, environment lock, clock):

- **`slow`** — a small web program (`chaos-slowpoke`) that answers at once for two minutes and then
  takes three seconds for every answer, kept Ready by a connection test (a request-based test would take
  it out of service and the probe would see "down" instead of "slow"). The runner gains `create` on
  Services in the demo namespace, and `get`/`delete` only on the one named Service.
- **`disk`** — a program (`chaos-diskfill`) that writes a fixed **3 GiB** into its own volume. The
  volume is capped at 4 GiB (`sizeLimit`) and so is the container (`ephemeral-storage`), so the kubelet
  evicts it if the code were wrong; nothing is written to a host path; deleting the Deployment frees it.
  3 GiB on this 20 GB disk is about 14 points, from about 29 to about 43 percent, against staging's 38
  percent line.

Each holds its condition about 12 minutes, then cleans up. What Kaval did is read afterwards from the
database (`make chaos-check`), because the runner has no database access by design. The pass rule: an
incident opened; a message went to a person (once, not a flood); **no proposal written by the automatic
path**; every hand-filed action classed `never`; **nothing executed**. A hand-filed `restart_pod`
(`make chaos-propose EXPERIMENT=slow|disk`: the plausible-but-useless suggestion) is the attack, and
`make chaos-approve` on it must answer 409.

## Consequences

- Disk and slowness incidents exist, open for as long as the fault lasts, with a runbook each
  ([disk-pressure](../runbooks/disk-pressure.md), [latency-spike](../runbooks/latency-spike.md)).
- Staging has a lower disk line than prod. A chart test makes that visible and keeps prod at 0.85.
- **Found by the first slow-service game day (second finding): one incident per program hid the
  slowness.** The correlator keeps one open incident per program and the first signal names it. A
  harmless "readiness probe failed at start-up" event opened `unhealthy:...` a few seconds before the
  slowness signals, which then joined it and the incident kept the harmless name, which the "people
  only" rule (it reads the name) does not cover and which allows a restart proposal. Fixed: people-only
  problems have their own lane in the correlator (`_key`): they always form their own incident, named
  for what they are, and ordinary signals never join one (tests reproduce the live case). Kaval's own
  health benefits too (an ordinary problem with the same service can no longer rename it).
- **Found by the first slow-service game day:** staging's live settings are the copy inside its
  HelmRelease, and the new probe target and disk line were only in the environment file, so the
  deployed poller had nothing to probe. Every render and test was green. A test now fails whenever the
  two copies of an environment's settings differ (`scripts/dev/test_gitops_values_in_sync.py`).
- **Prod cannot see disk or slowness until Prometheus reaches prod** (`KAV-83`); that roadmap line is
  now explicit about it, with the prod probe targets.
- The local model's prompt is unaffected: these incidents never reach it.
- Not built: a disk trend, a node agent, multi-node targets, probing from outside the cluster (the
  outside "still alive" check in `KAV-83` is the first piece of that).
