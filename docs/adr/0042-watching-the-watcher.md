# ADR-0042 — Watching the watcher: health numbers, "stopped" alerts, and telling a person

- **Status:** Accepted — built and tested (unit, real Postgres, promtool); live proof in [Lab 45](../labs/lab-45-watching-the-watcher.md)
- **Date:** 2026-10-11
- **Deciders:** Roshan
- **Story:** `KAV-78` · second slice of Phase 5 (`KAV-10`), part of `KAV-76` (Prometheus on staging, then prod)

## Context

Kaval watches other things. Nothing watched Kaval. If the collector, agent or executor froze, the
only symptom was that nothing happened, which looks exactly like a quiet day. A chaos test (the rest
of Phase 5) that reports "no incident was opened" is meaningless if the thing that opens incidents
could have been dead. So before any fault is injected, Kaval has to be able to say when it has
stopped, and the saying must not depend on the part that stopped.

Three facts about the code shaped the design:

1. **Four of the five services are loops with no web port.** Only the gateway answers requests.
   A pod's own health check can only ask "is the process alive", and a process stuck waiting on
   the database is alive.
2. **Slack posts only proposals waiting for approval**, never incidents. Routing "Kaval stopped"
   through incident → proposal → Slack would never reach a person, and would route it through the
   very correlator and agent that may be the broken part.
3. **The cluster's network (Flannel) cannot restrict which pod reads which port**, so anything a
   service publishes about itself is readable by every pod.

## Decision

### Every service shows a small health page, and nothing sensitive is on it

`kaval_shared.metrics` serves `GET :9100/metrics` from every service. Kaval's own numbers are three:
when the last full pass finished, how often a pass is meant to happen, and how many passes failed;
plus the standard process numbers (memory, CPU), which is what turns "how much of the 4 GB does each
service use" into a measurement. Labels are the service name and version only; a test fails the
build if any other label appears. No incident ids, targets, fingerprints or error text, ever. If the
port cannot open, the service says so once and carries on without it. The page is on its own port,
not the gateway's API port, so a future ingress for the API could never expose it.

The library is `prometheus-client`, the reference implementation of the format, as a core
dependency (pure Python, no dependencies of its own). Hand-writing the format with the standard
library would have saved one package and risked subtle escaping bugs in the one place a bug means
silent monitoring.

### Prometheus reads them, and judges them with three rules

On in staging first (`prometheus.enabled`); prod follows in `KAV-83`. Each service has a headless
Kubernetes Service so Prometheus finds every pod by plain DNS, with no Kubernetes API permission
added to it. The rules (`deploy/charts/kaval/files/kaval-alerts.rules.yaml`, tested by `promtool`):

- **KavalLoopStalled** — the last finished pass is older than four intervals (never under 2 minutes),
  for 1 minute. This is the alive-but-doing-nothing failure.
- **KavalTargetDown** — a service cannot be reached for 2 minutes, **or has no `up` series at all**
  (`absent()`): a pod that does not exist is not "down", it is missing, and a rule on `up == 0` alone
  is silent about it.
- **KavalWatchdog** — always firing, deliberately. If Prometheus answers but this is not in the list
  of firing alerts, its rules are not being evaluated, and no rule can report that about itself.

### The collector's poller turns firing alerts into signals

The poller that already reads Prometheus for memory pressure also reads which Kaval alerts are
firing and writes each as the `alert_firing` signal the correlator already understands. Polling a
list is what makes Alertmanager unnecessary ([ADR-0043](0043-alertmanager-not-adopted-yet.md)). The
poller also watches Prometheus itself: three passes in a row with no answer becomes
`kaval_prometheus_unreachable`, and an answering Prometheus without the watchdog becomes
`kaval_watchdog_missing`. These are written to the database, which is up when Prometheus is not.

### Kaval's own faults are for a person, never for a fix

A cause whose name starts `kaval_` ([`kaval_shared.health`](../../services/shared/kaval_shared/health.py))
is Kaval's own health. `write_proposal` refuses to write a proposal for one, the one place every
proposal passes through. The executor's cluster permissions cover only its demo namespace, so it
could not restart Kaval's pods anyway; refusing at the source also stops a pending approval that
looks like a fix and can never work, and stops a 1B model being asked to guess at its own failure.

### A person is told straight from the signals

`kaval_gateway.health_alerts` posts each fresh fault to Slack directly from the `signal` table, once
per 30 minutes per fault, with no buttons and a pointer to
[the runbook](../runbooks/kaval-stopped-reporting.md). It does not wait for the correlator to open an
incident. Each message is recorded in a new insert-only `notification` table, written only after
Slack accepted it: a Slack failure leaves no record and the next pass tries again. The text is a
fixed sentence and a service name, so the table is safe to copy to staging with the rest of the
database (`anonymise.sql`, classified in the test).

The alert path may therefore depend on Prometheus, the poller, the database and the gateway, and on
nothing it reports about. The correlator still opens a `kaval_*` incident for the history and the
dashboard when it is alive.

### The dashboard is a file in git

[`deploy/dashboards/kaval-health.json`](../../deploy/dashboards/kaval-health.json) is a Grafana
dashboard (services reachable, seconds since each last finished a pass, failed passes, each
service's real memory, container memory against its limit, Prometheus's own size, which alerts
fired). It is shown by a Grafana run where it is needed (the dev server or the laptop) against a
port-forward to the environment's Prometheus, not on the 4 GB node. Dashboards as files in version
control is the usual practice, and a viewer that is only wanted during a session should not cost
memory the rest of the time.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Rely on Kubernetes liveness/readiness probes | They see a dead process, not a stuck one. Three of the four loops have no port to probe. Probes remain as they were |
| Each loop writes its own heartbeat row to the database, a watcher reads the table | Reinvents what Prometheus does and adds database writes every few seconds from every service. A metric that costs nothing to hold is the standard tool |
| Alert on `up == 0` only | Silent when the pod is gone entirely (no series). `absent()` is added for exactly that |
| Send "Kaval stopped" through incident → proposal → approval | The messenger would be the stopped part, and a fix proposal for Kaval itself is the wrong output |
| Grafana running on the node | About 100 MB for a screen looked at during a session. Revisit if the dashboard is wanted without a laptop |
| An outside "still alive" ping service now | Staging and prod are parked most of the time; a naive ping would alarm on every park. Needs a decision on how parking and pinging fit together, taken with the production rollout (`KAV-83`) |

## Security

- The health pages carry only service names, versions, counts and times, enforced by a test. They are
  readable by any pod, which is acceptable only because of that.
- Prometheus gains no permission: scraping Kaval's services is DNS, not the Kubernetes API. It keeps
  its read-only access to node metrics and nothing else.
- The poller still has no Kubernetes token. It writes signals, as before; `kaval_collector` already
  holds `SELECT, INSERT` on `signal`.
- The gateway's database role gains `SELECT, INSERT` on `notification` only.
- A confused model cannot be made to act on Kaval's own health: no proposal can be written for it.

## Failure and rollback

- A service whose health port cannot open keeps working; the cost is that Prometheus reports it
  unreachable, which is the honest outcome.
- Prometheus down: the poller raises `kaval_prometheus_unreachable` after three missed passes
  (about 90 s). The poller down: Prometheus raises `KavalTargetDown` for it, and the alert waits in
  the list until the poller returns.
- Switching it off: `prometheus.enabled: false` in the environment's values. The health ports stay
  (inert). The notification table and the two grants are harmless to leave.
- **Known gaps, recorded rather than hidden:**
  1. **If the gateway is the broken service, no Slack message is sent.** Only the incident row and
     the dashboard show it. Closing this needs an outside check, decided with `KAV-83`.
  2. **If Prometheus and the poller are both down, nothing raises it.** Same remedy.
  3. **Staging has no Slack app of its own** (a second Socket Mode app on the same workspace would
     split events with prod). The Slack hop is proven by test and in production's first session
     with this enabled; in staging the proof is the signal and incident rows.
  4. **No "recovered" message.** The incident closes itself after 15 quiet minutes; Slack says nothing.
  5. Thresholds (four intervals, two-minute floor) are provisional until the first game day.

## Observability

This ADR is the observability of the rest: the dashboard above, the alerts, and `make mttr`. The
feature is itself watched by the same rules (the poller and Prometheus have a health page and a rule
each) and by the watchdog.

## Cost and capacity

No AWS cost. Memory, measured, not guessed: on the local cluster each service's resident memory
is 62–98 MiB and Prometheus 89 MiB (RSS), the container working set of Prometheus 38 MiB. The
figure that counts is the one on staging's real 4 GB node, recorded in [Lab 45](../labs/lab-45-watching-the-watcher.md)
and the [memory budget](../architecture/overview.md#runtime-footprint).

## Consequences

- Needs the `notification` migration and the gateway's grant (`db-roles.sql`, re-applied from `main`
  by each node on boot and every six hours, [ADR-0027](0027-automatic-secret-and-role-recreation.md)).
- Every image gains `prometheus-client` (the lock file changes, so the images are rebuilt and pass
  through staging like any other change).
- `make rules-test` runs the alert rules through `promtool` using the image the chart pins; CI runs it.
- Slice 3 onward can rely on being told when Kaval, not only the cluster, fails during a chaos run.
