# Incidents are not getting a diagnosis

An incident is open, it is one Kaval would normally diagnose, and no proposal has appeared for it. The diagnosis
worker (`diagnoser`) asks the language model about each new incident by itself; when that stops, nothing is
proposing fixes. This is **for a person**: there is no automatic fix for the thing that makes the fixes.
Design: [ADR-0055](../adr/0055-the-model-runs-in-the-cluster-and-diagnoses-new-incidents.md).

## Signals

- A Slack message "Kaval's own health: an incident has waited over 15 minutes for a diagnosis"
  (`KavalDiagnosisBacklog`, incident `kaval_diagnosis_backlog:...`), or "the diagnosis worker is not running"
  (`KavalDiagnoserMissing`, `kaval_diagnoser_missing:...`)
- `KavalLoopStalled` for `diagnoser`: the worker itself stopped finishing passes
- On the metrics page: `kaval_diagnosis_waiting` above 0 for a long time, `kaval_diagnosis_oldest_wait_seconds`
  growing, `kaval_diagnosis_total{outcome="model_error"}` rising
- An open incident with no proposal row, and `make mttr` showing no diagnose time for it

What does **not** count: incidents about Kaval itself (`kaval_*`), a full disk, or a slow service. Those are for a
person by design and the worker skips them. An incident older than four hours is also left for a person.

## Likely causes

Ordered by how often they should happen.

1. **The model pod is down or restarting.** It was killed for memory (its limit is 1.5 GiB) or is downloading the
   model again after a restart (about a minute)
2. **The model is too slow.** A diagnosis takes longer than `diagnoser.timeoutSeconds` (90 s) because the node's
   processor is busy, so the worker backs off (30 s, then doubling to 10 minutes)
3. **The hourly limit is holding incidents back.** Six proposals an hour is the default; a flapping workload can
   use them all. The waiting incidents are real, and the limit is working as designed
4. **The model's id is not the pinned one** and the model pod will not start (`REFUSING` in its init log)
5. **The worker is not running** (a bad release, out of memory, node pressure): same checks as
   [kaval-stopped-reporting](kaval-stopped-reporting.md)
6. **The node is short of memory** and the kernel is killing things
7. **The worker's database account is missing a permission.** Its log ends in `permission denied for table ...`
   and the pod restarts (the restart count climbs, then it idles). Seen once, on `outcome`, 2026-10-15
8. **The incident's approval request expired.** The worker never makes a second proposal for an incident that
   already has one, so an unanswered request (staging: 15 minutes, prod: 4 hours) leaves the incident with no live
   proposal. This is by design, and it is a person's call

## Diagnosis

On the node, through Session Manager (staging: `make staging-status` shows the way; prod: the same, read-only first).

```bash
kubectl get pods -n <kaval namespace>                               # ollama and diagnoser: Running? restarts?
kubectl describe pod -n <ns> -l app.kubernetes.io/component=ollama | tail -25   # OOMKilled? init failed?
kubectl logs -n <ns> deploy/<release>-ollama -c fetch-model          # "REFUSING" = wrong model id
kubectl logs -n <ns> deploy/<release>-diagnoser --tail=30            # model_error / rate_limited / paused
kubectl top pods -n <ns>; kubectl top node                           # who is using the memory
```

The worker's log says what it did each pass: `ok <incident> ...`, `model_error <incident> ... <the error>`, a
`paused after a failure` line (it will try again), `rate_limited`, or `nothing waiting`. Which incidents are waiting:

```sql
SELECT i.id, i.fingerprint, now() - i.opened_at AS waiting
FROM incident i LEFT JOIN proposal p ON p.incident_id = i.id
WHERE i.closed_at IS NULL AND p.id IS NULL AND i.fingerprint NOT LIKE 'kaval\_%'
ORDER BY i.opened_at;
SELECT count(*) FROM proposal WHERE created_at > now() - interval '1 hour';   -- near 6? the limit
```

## Remediation

1. **Model pod down:** `kubectl rollout restart deploy/<release>-ollama -n <ns>` (reversible, no data lost: the
   model is downloaded again). Wait for it to be Ready, then the worker's next pass proposes
1b. **Permission denied:** the grants live in `scripts/ops/db-roles.sql`; add the missing read-only line by pull
   request. The node re-applies the file from `main` every 6 hours, or at once with
   `systemctl start kaval-gitops-bootstrap.service` over Session Manager. Never grant the worker more than it reads
2. **Model too slow:** look at `kubectl top node`. If something else is eating the processor, deal with that. Do
   not raise the timeout first: a slow model means the node is busy, and waiting longer only queues more
3. **Hourly limit:** read the incidents. If one workload flaps, fixing that is the answer. To clear a real
   backlog, raise `diagnoser.maxPerHour` in the environment's values file through a pull request (Flux applies it)
4. **Wrong model id:** someone changed the model upstream or the pinned id. Do not edit the id to "make it pass":
   find out what changed, check the new model with `make evals`, then change `ollama.modelId` in a pull request
5. **A person can always propose by hand** (also the answer to an expired approval): `make diagnose INCIDENT=<id>`
   from the laptop (with the tunnel) files the same proposal the worker would have
6. **Turn the whole thing off:** set `diagnoser.enabled: false` (and `ollama.enabled: false`) in the environment's
   values file and merge. Incidents are still found and approved fixes still run; only the automatic diagnosis stops

## Do not

- Do not `exec` into the worker or the model pod to run something: a second process in a small container is how
  this project once killed its own agent. Use the laptop or a Job
- Do not delete proposal rows to make an incident "waiting" again. They are the record; use `make diagnose`
- Do not raise the model's memory limit above the node's spare memory. If it does not fit, the answer is a
  smaller footprint or a decision about the node, not a limit the kernel will not honour
