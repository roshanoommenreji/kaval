# A service has become slow

Kaval timed a small request to a service and it took longer than 1 second, or did not come back within
5 seconds, three checks in a row (about a minute and a half). This is **for a person**: Kaval posts to
Slack and proposes no fix, because a restart does not make a slow service fast and usually throws away
the evidence ([ADR-0047](../adr/0047-incidents-that-are-for-people-disk-full-and-slowness.md)).

## Signals

- Slack: ":warning: Needs a person: `<service>` is answering slowly (or not in time)", with this
  runbook; a reminder every 30 minutes while it lasts, and one louder `@here` message after 2 hours
- An incident `latency_high:k8s:<namespace>/<service>`, severity medium
- The service still answers, and its pods are Ready: this is not a crash, a restart loop or an
  out-of-memory kill (those have their own incidents and runbooks)

## Likely causes

1. **The machine is short of CPU or memory** (a 4 GB node shared by everything; the page cache and swap
   make everything slow before anything is killed)
2. **A slow dependency**, most often the database (the checks of the service's own `/healthz` show it)
3. **A new release** that is slower (check when it was deployed against when the alert began)
4. **CPU throttling:** the container is held to its CPU limit
5. **A burst of traffic or a heavy job** (a backup, an index, a game day)

## Diagnosis

```bash
# what the machine is doing
uptime; free -m
k3s kubectl top nodes 2>/dev/null || true       # may not exist; free and uptime are enough
# the service's pods and when they last changed
k3s kubectl get pods -n <namespace> -o wide
k3s kubectl describe deployment <service> -n <namespace> | grep -iE "image|limits|cpu"
# is the database slow? from the gateway's own check
k3s kubectl exec -n <release-namespace> deploy/<gateway> -- python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/healthz').read())"
```

Compare the time the alert began with the last release (`make staging-status`, or the release history in
`docs/releases/`). Throttling shows in the container metrics as `container_cpu_cfs_throttled_seconds_total`
growing.

## Remediation

Depends on the cause, and it is your call:

- Machine short of memory or CPU: find the biggest user, scale the noisy one down, or move to a bigger
  node (a cost decision: update `docs/cost/budget-plan.md`)
- Slow dependency: see the database runbooks; a slow database is not fixed from the service
- Slow since a release: **roll back** with the normal path ([`rollback-prod.md`](rollback-prod.md)) once
  you have confirmed the timing, then investigate off the clock
- Throttled: raise the CPU limit for that service in its values file and release normally

## Do not

- **Do not restart the service first.** It will usually be fast for a minute and then slow again, and
  you will have lost what the slowness was telling you
- Do not approve any fix Kaval seems to offer for this incident: there should be none, and the system
  will refuse one
- Do not raise the alert's limits to make it go quiet; a slow service is a real problem for whoever
  is using it

## After

- The incident closes by itself about 15 minutes after the service is fast again
- Write down the cause; if it was the machine's size, that is an input to the capacity numbers
  (`docs/cost/`) and to the headroom re-measure in `KAV-83`
