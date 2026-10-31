# ADR-0068 — Two health pages for the gateway, and every pod passes Pod Security `restricted`

- **Status:** Accepted (plan approved by Roshan 2026-10-31)
- **Date:** 2026-10-31
- **Deciders:** Roshan
- **Stories:** `KAV-106` (health pages) and `KAV-105` (pod security), built as one package · both found live in the EKS lab ([Lab 66](../labs/lab-66-the-eks-lab-live-two-sessions-and-what-they-found.md), findings 5 and 9) · builds on [ADR-0020](0020-the-helm-chart-and-the-local-k3d-environment.md) (the chart and why `/healthz` was never a probe), [ADR-0051](0051-closing-phase-5-readiness-checks-prod-prometheus-and-the-disk-trend.md) (readiness on the workers), [ADR-0067](0067-the-eks-lab-is-ephemeral-has-its-own-timer-and-is-proven-clean-by-a-label-sweep.md) (the lab)

## Context

Two weaknesses in the shared chart came out of the live EKS run. Neither is an EKS problem; EKS only made them visible.

1. **One health page answered two different questions.** `/healthz` reports the database *and* the language model and answers 503 if either is down. The gateway's checks therefore could not use it (it is 503 by design wherever no model is deployed, which is every environment), so they were a plain "is the port open" test. A gateway cut off from its database still counted as healthy, and the load balancer had to be pointed at `/openapi.json`, a page that proves nothing about readiness.
2. **None of the chart's pods carried the standard lock-down settings.** The chart passed Pod Security `baseline` and failed `restricted` on all seven pods: no `allowPrivilegeEscalation: false`, no dropped capabilities, no `runAsNonRoot`, no `seccompProfile`. The k3s clusters we run enforce neither level, so nothing had ever said so.

## Decision

### 1. Two new pages; the old one is unchanged

| Page | Question | Needs | Answers |
|---|---|---|---|
| `/livez` | Is this program alive? | nothing | 200 always, with the version |
| `/readyz` | Can it do its job? | a reachable, migrated database | 200, or 503 `degraded` |
| `/healthz` | The full report | database and model | unchanged (200 or 503) |

Liveness depends on nothing because a restart cannot repair a database or a model that is down; a liveness check that includes them turns an outage into a restart storm. Readiness is what the gateway itself needs, the database. The language model is the diagnosis worker's dependency and not the gateway's, so it is in neither probe. `/healthz` stays for the watcher (Prometheus polls it) and for a person with `curl`. Like `/healthz`, both pages put only the exception's **type** in their answer, never its message, because they are unauthenticated.

| Alternative | Why not |
|---|---|
| Change `/healthz` to mean "ready" | The watcher and the staging smoke test read it as the full report; changing its meaning silently breaks both |
| Put the model in `/readyz` | The API would leave service in every environment that has no model, which is all of them |
| Keep the port check | Cannot tell "up" from "cut off from its database" |

The chart's gateway uses `/livez` for the liveness probe and `/readyz` for readiness. The EKS lab's load balancer keeps `/openapi.json` until the lab next runs on an image that has the new pages (the lab pins its own tag; it was not run again).

### 2. Pods are locked down in the templates, not in values

Every pod and every init container in the chart gets: runs as a **non-root numeric user**, the runtime's default system-call filter (`seccompProfile: RuntimeDefault`), no privilege escalation, all Linux capabilities dropped. The Kaval images already run as user 10001 (`useradd --uid 10001` in each Dockerfile), so one shared helper (`kaval.podSecurity`) supplies the pod-level part. Prometheus (65534), Ollama (65534) and Postgres (999) run as the user their images were built for.

It lives in the templates and not in a setting: a pod that is not locked down is a defect in every environment, not a per-environment choice, and the portability rule (environments differ only by values and digest) means it goes everywhere at once.

The in-cluster Postgres (local, k3d and the lab; staging and prod use a separate database server) is the one with a real cost. A non-root Postgres cannot take over the disk's top folder, so its data now lives in a subfolder (`PGDATA=/var/lib/postgresql/data/pgdata`) and the pod has `fsGroup: 999`. A database left in the old place by an earlier release is simply not seen; these databases are rebuilt from nothing every time.

### 3. A test fails if a pod is not locked down

`scripts/dev/test_chart_security.py` renders the chart for all four environments and checks every pod and init container against the `restricted` list, with a negative test that proves the checker reports a loose pod. A second test fails if any probe is pointed at `/healthz`. This is the same kind of guard as the label test for the network policies (Lab 66, finding 6): a rule that "applied" proves nothing, a check that fails on the old file does.

## Consequences

- Staging verifies it before production: the staging smoke test's "pods running and ready" check is the proof for the pages (a new image without `/readyz` would never become ready), and its other checks prove the locked-down pods start and work. Production picks it up through the normal promotion gate at its next bring-up.
- A gateway that loses its database now stops receiving traffic, instead of failing requests while looking healthy. With two copies and a shared database, both go unready together, which is the truth.
- The chart now passes `restricted`. The lab's `baseline` enforcement can be raised to `restricted` the next time the lab runs; that is a lab-doc step, not a code change.
- Cost: nothing in production; one staging run (about $1) shared with other changes.

## What would make us revisit

A second chart dependency that needs root (a database image or a driver). That would be a reason for a namespace-level exemption written down with its reason, not a quiet loosening of the template.

## Amendment 2026-10-31 — the first staging run found a fourth thing

The staging run for this change did exactly what staging is for. The first pod (the database migration) never left its start-up wait: the wait runs `pg_isready` from the stock Postgres image, and with the pod now running as user 10001 (an account that image does not have) `pg_isready` cannot work out a default database user and answers "no attempt" for ever. Reproduced locally in seconds: as 10001 the old command says "no attempt", with the user named (`-U "$POSTGRES_USER"`, which every database Secret already carries) it says "accepting connections". All seven start-up waits (agent, collector, the Prometheus poller, cost, diagnoser, executor, migrate) now name the user. `pg_dump` (the backup job) already did, and was re-checked as 10001. A new chart test fails if any `pg_isready` in any environment omits `-U`, with a negative case. The lesson is the one the Decision already states: rendering and the cluster's dry run prove a pod is admitted, not that it works; only running it does.
