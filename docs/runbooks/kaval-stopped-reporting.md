# Kaval itself has stopped working

One of Kaval's own services has stopped doing its job, or Prometheus (the thing that notices) cannot
be reached. This is **for a person**: Kaval proposes no fix for its own health, and the executor
could not apply one (its cluster permissions cover only its demo namespace, never Kaval's pods).
Design: [ADR-0042](../adr/0042-watching-the-watcher.md).

## Signals

- A Slack message "Kaval's own health: `<service>` has stopped finishing its work", "cannot be reached
  at all", "Prometheus cannot be reached" or "its alert rules are not running"
- An incident whose fingerprint starts `kaval_` (`kaval_loop_stalled:k8s:kaval-staging/executor`,
  `kaval_target_down:k8s:...`, `kaval_prometheus_unreachable:k8s:.../prometheus`,
  `kaval_watchdog_missing:...`), always high severity
- `make mttr` or `outcome` rows stop growing while problems are known to exist
- **No news is not good news here.** If this whole page is the *only* way you would find out and
  Slack is silent, remember that the gateway is the messenger: see "The messenger itself" below

## Likely causes

Ordered by how often they actually happen.

1. **The database is slow or locked.** The service is alive and its port answers, but its pass is
   waiting on Postgres. This is what `KavalLoopStalled` ("running, but doing nothing") nearly always is
2. **A pod was killed and is restarting** (out of memory, node pressure, a rollout). Gone in a minute
   or two; `KavalTargetDown` fires only after two
3. **The node is short of memory** and the kernel is killing things. Check the node before blaming a service
4. **A bad release**: the new image crashes on start. The pod goes round `CrashLoopBackOff`
5. **Prometheus itself is down or out of memory** (`kaval_prometheus_unreachable`)
6. **Prometheus is up but its rules are not being evaluated** (`kaval_watchdog_missing`): a broken
   rules file after a change

## Diagnosis

On the node, through Session Manager (staging: `make staging-status` shows the way; prod: the same,
read-only first).

```bash
kubectl get pods -n <kaval namespace>                     # which pod, how many restarts
kubectl describe pod -n <ns> <pod> | tail -20             # OOMKilled? CrashLoopBackOff? Evicted?
kubectl logs -n <ns> deploy/<release>-<service> --tail=50 # the last thing it said
kubectl top pods -n <ns>; kubectl top node                # memory pressure
```

For a **stalled** service (alive but not finishing), ask the database what it is waiting for:

```sql
SELECT pid, state, wait_event_type, now() - xact_start AS open_for, left(query, 80)
FROM pg_stat_activity WHERE datname = 'kaval' ORDER BY xact_start;
SELECT * FROM pg_locks WHERE NOT granted;                  -- anyone waiting for a lock?
```

For **Prometheus** (`<release>-prometheus`): `kubectl logs deploy/<release>-prometheus`. A rules file
that does not load is named in its first lines.

Look at the Kaval health dashboard ([deploy/dashboards/kaval-health.json](../../deploy/dashboards/kaval-health.json))
for the shape: when did the last pass finish, what happened to memory just before.

## Remediation

Each is reversible; the blast radius is Kaval itself, never the cluster's workloads.

- **Locked or slow database:** end the long transaction that holds the lock
  (`SELECT pg_terminate_backend(<pid>)`), after checking it is not a restore or a migration.
  The stalled service resumes on its own on its next pass.
- **Killed or crash-looping pod:** read why before restarting. `OOMKilled` means its memory limit is
  too low: raise it in `deploy/charts/kaval/values.yaml` through the normal pull request, not by hand.
  A bad release: [roll back](rollback-prod.md).
- **Node out of memory:** stop something that is not needed (staging has a four-hour idle stop; prod is
  parked outside sessions), then find what grew. Headroom is tracked in
  [the overview](../architecture/overview.md#runtime-footprint).
- **Prometheus down:** `kubectl rollout restart deploy/<release>-prometheus`. Its data is only the last
  two hours and the signal table is the record, so nothing is lost by restarting it.
- **Rules not evaluated:** `make rules-test` on the change that broke them; revert it.

## Do not

- Restart Kaval's services by hand just to clear the alert before knowing why they stopped: the next
  one will be harder to explain
- Terminate a database session you have not identified: it may be a restore or a migration
- Change an alert rule to make the message stop. A rule that is wrong is fixed and tested
  (`make rules-test`); one that is merely inconvenient is the system working

## The messenger itself

The Slack message is posted by the gateway. If the gateway is the broken service, nobody is told by
Slack, and the only trace is the incident row (if the agent is up) and the dashboard. This gap is known
and recorded in ADR-0042: the fix is an outside "still alive" check, decided with the production rollout
(`KAV-83`) because production is parked most of the time and a naive check would alarm on every park.
Until then, **look at the dashboard when you start a session.**

## Verify

- `kubectl get pods` all `Running`, restarts not climbing
- The dashboard shows each service's "last pass" under a minute old
- The `kaval_` incident closes by itself after 15 quiet minutes
