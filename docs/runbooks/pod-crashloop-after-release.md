# A pod keeps crashing right after a new release (CrashLoopBackOff)

A Deployment was updated to a new version and the new pods start, fail and are restarted again and
again (`CrashLoopBackOff`). With a normal rolling update the old pod keeps running while the new one
crashes, so the service may still answer while the rollout is stuck. Restarting the pod does not help:
it comes back running the same broken version. Going back to the previous version does.

## Signals

- Pod status `CrashLoopBackOff` or `Error`, restart count rising, on a pod that is NEW (young age)
- Kubernetes events `BackOff` / `Back-off restarting failed container` for the same pod
- The last exit reason is `Error` with a non-zero exit code (1, 2, 126, 127), **not** `OOMKilled`
  (that is [`pod-oomkilled.md`](pod-oomkilled.md): a different fix)
- `kubectl rollout status` hangs; the Deployment shows more replicas than ready ones
- An incident whose cause is `crashloop` (Kaval records the pod's last exit reason in the signal)

## Likely causes

Ordered by how often they happen.

1. **The new version is broken** (a bug at start-up, a missing file, an exec format error)
2. **The new version needs configuration that is not there** (an environment variable, a Secret, a
   ConfigMap key the release assumes)
3. **A dependency is down or refusing it** (database, an upstream service), so the program exits
   at start. Rolling back will not help; check the dependency first
4. **Wrong image** (wrong architecture: `exec format error`; a tag that does not run)

## Diagnosis

```bash
kubectl get pods -n <ns>                                           # young pod, restarts, status
kubectl logs -n <ns> <pod> --previous                              # why it ended
kubectl describe pod -n <ns> <pod> | grep -A8 "Last State"         # Reason: Error, Exit Code
kubectl rollout history deployment/<name> -n <ns>                  # is there an earlier revision?
kubectl rollout history deployment/<name> -n <ns> --revision=<n>   # what changed between versions
```

Tell cause 1/2 from 3: read the log. A crash that names the program's own error (missing variable,
syntax error) is the release. A crash that says it cannot connect to something is the dependency;
fix that and leave the release alone.

## Remediation

- **Roll back to the previous version** of the Deployment (not of the pod; a pod edit is lost at the
  next restart). Kaval's action `rollback_deployment` does this: it puts the previous revision's pod
  template back (the same as `kubectl rollout undo`), asks for a person's approval first, records the
  version it left, and **refuses** when there is no earlier version, when the earlier version is the
  same as the current one, or when a rollback was already done in the last hour. Blast radius: that
  Deployment (its pods are replaced). Reversible: yes (rolling forward is the same operation).
- If the Deployment is deployed by Flux from git (Kaval's own services are), a direct rollback stands
  only until the next release; **also revert the commit in git**, or the next release brings the bad
  version back. See [ADR-0045](../adr/0045-rolling-back-a-bad-release-and-flux-versus-direct-fixes.md).
- Afterwards, check the program stays up for several minutes and that the rollout finished
  (`kubectl rollout status`), then work out why the release was bad before shipping it again.

## Do not

- **Do not restart the pod** as the fix. It returns running the same broken version and crashes again
- **Do not roll back twice in a row**: the second rollback goes forward to the broken version again
- Do not roll back when the logs say a dependency is down: the old version would crash the same way
- Do not delete the Deployment to "start clean": the rollout history, which is the way back, goes with it
