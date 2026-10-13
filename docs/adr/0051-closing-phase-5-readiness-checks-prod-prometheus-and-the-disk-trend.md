# ADR-0051 — Closing Phase 5: readiness checks, Prometheus in production, and the disk trend

- **Status:** Accepted — Part 1 (readiness checks, production settings, the chart and the disk-trend
  decision) built and tested; the staging proof and the production roll-out are recorded in
  [Lab 50](../labs/lab-50-closing-phase-5.md). The outside "still alive" check is decided in Part 2.
- **Date:** 2026-10-13
- **Deciders:** Roshan
- **Story:** `KAV-83` (Phase 5, slice 7) · umbrella `KAV-76`

## Context

Five slices proved Kaval on staging. Production still runs the version promoted on 2026-10-08, which
has none of Phase 5: no health pages, no Prometheus, no approval expiry, no database monitoring.
Three findings from the game days are still open: a bad release of a background program takes the
working copy down too, nothing watches production's own disk or speed, and the disk trend was
deferred. This ADR closes those and sets the order production must follow.

## Decision 1 — a readiness check on every background program

The agent, the executor, the collector and the Prometheus poller each open a health page on `:9100`
only after their start-up work succeeded (the database wait, the cluster login, the settings check).
A `readinessProbe` that connects to that port means "this copy got as far as working". Without a
check, Kubernetes counts a copy as ready the moment its process exists, so a release that dies a few
seconds later has already replaced the good copy (found by the bad-release game day, ADR-0045).
With one, a rolling update waits for the new copy; the old one keeps working until it is ready.

- **Readiness only, no liveness check.** A restart because the database is slow makes a bad hour
  worse, and a stalled loop is already the job of `KavalLoopStalled` (ADR-0042).
- **One shared definition** (`kaval.workerReadiness` in `_helpers.tpl`) so the four cannot drift apart;
  a chart test renders staging, prod and local and checks each.
- Prometheus already scrapes pods that are not ready (`publishNotReadyAddresses`), so a stuck copy
  stays visible to `KavalTargetDown`.
- A copy that cannot open the page (the port taken, or `KAVAL_METRICS_PORT=0`) would never become
  ready. The chart never sets either; if someone does, the rollout stops loudly rather than silently.

Rejected: **an HTTP `/ready` endpoint** that also tests the database (more code in four programs, and
a database blip would take every copy out of service at once); **a startup probe** (the same signal,
with no extra value here).

## Decision 2 — Prometheus, the database exporters and a gateway check go to production

`deploy/environments/prod/values.yaml` (and the copy Flux reads, kept equal by a test) switches on
`prometheus`, `prometheus.database` and times one service: Kaval's own gateway, with the same request
a person's browser would make. The disk line stays at the chart's 0.85; production never gets chaos
(a test renders production and fails on any chaos object).

**Order matters.** Flux deploys the chart from `main` with the image tag written for production. The
readiness checks need images that serve the health page, so production's tag must be promoted to a
version that has it **before** production is next brought up. A fresh install on the old tag would
leave the three programs never ready. The production runbook says so, and the roll-out in Lab 50
promotes first.

## Decision 3 — the disk trend is not built

A "full within N hours" prediction was deferred in [ADR-0047](0047-incidents-that-are-for-people-disk-full-and-slowness.md)
because Prometheus keeps two hours. The decision now:

- **App server (20 GB):** it is created fresh each time production is brought up, so its disk resets
  every day. A trend across days is meaningless; the 85 percent line (which leaves 3 GB) is the right
  tool.
- **Database server (20 GB data volume):** this one persists and grows with real data. At present it
  holds almost nothing (the first restore drill restored 24 KB). A trend becomes worth building when
  the data volume passes about half full, not before; until then the 85 percent line speaks well before
  anything breaks.
- **Not built, with a trigger written down:** revisit when the database data volume passes 50 percent,
  or production runs unparked for days at a time. The upgrade then is a longer Prometheus retention
  for that one series (or a daily point in the signal table), not a node agent.

## Decision 4 — the runbooks are complete for the five failure classes

Memory kill, bad release, lost server, full disk and slow service each have a runbook written with
their experiment. The audit against the alerts found no uncovered alert (`KavalDatabaseDown` and
`KavalDatabaseMonitorDown` are in `kaval-stopped-reporting.md` and `disk-pressure.md`). One runbook is
added with the production roll-out: how to bring production up, verify and park it again
(`prod-rollout.md`). `cost-spike.md` stays with Phase 6, where its signal arrives.

## Decision 5 — the time-to-restore chart is drawn from the recorded runs

Staging is destroyed after every game day and takes its database, and so `make mttr`, with it. The
runs are therefore copied from each post-mortem into `docs/architecture/mttr-runs.json`;
`scripts/dev/mttr_chart.py` draws `docs/architecture/mttr.svg`; a test refuses numbers that do not
add up (stages must sum to the wall-clock time; machine time must leave out the person's wait) and a
picture that is out of date. Production's own numbers come from `make mttr` once it has a real fix.

## Consequences

- Six runs on one chart is a record, not a rate: the targets in [service levels](../architecture/service-levels.md)
  stay provisional until there are more.
- Turning the readiness check on means a bad release of a background program now shows as a stuck
  rollout (old copy still serving, new one `0/1`) instead of an outage. That is the intended failure
  shape; the runbook `pod-crashloop-after-release.md` says how to read it.
- Production's Prometheus costs about 100 MiB of the 4 GB node (staging measured 86 MiB), plus a
  second gateway that production already runs. The re-measure in Lab 50 is the proof.
