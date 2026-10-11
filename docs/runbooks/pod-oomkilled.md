# A container keeps being killed for running out of memory (OOMKilled)

A container used more memory than its limit, and Kubernetes ended it. The pod then restarts, uses the
same memory again, and dies again: `CrashLoopBackOff` with the reason `OOMKilled`. Restarting it does
not help, because it comes back with the same limit. Raising the limit, within reason, does.

## Signals

- Pod status `OOMKilled` (last state, reason `OOMKilled`, exit code 137) with a rising restart count
- Kubernetes events `BackOff` / `Back-off restarting failed container` for the same pod
- Kaval's `memory_pressure` signal: a container's working set at or above 90 % of its limit, *before*
  the kill, when memory grows gradually
- An incident whose cause is memory pressure or an OOM-killed container

## Likely causes

Ordered by how often they happen.

1. **The limit is simply too low** for what the program needs after a change (a new version, more data).
   The program is healthy, only boxed in
2. **A memory leak**: usage grows until any limit is reached. Doubling only delays it
3. **One bad input or request** that loads far more than normal
4. **The node is short of memory** and the kernel kills the biggest container (then other pods on the
   same node are hit too; check the node first)

## Diagnosis

```bash
kubectl get pods -n <ns>                                           # restarts, status
kubectl describe pod -n <ns> <pod> | grep -A6 "Last State"         # Reason: OOMKilled, Exit Code: 137
kubectl get deploy -n <ns> <deploy> -o jsonpath='{.spec.template.spec.containers[*].resources}'
kubectl top pod -n <ns> --containers                               # current use against the limit
```

Tell cause 1 from cause 2: look at memory over the last hour. A **flat line just under the limit** is
cause 1. A **line that climbs steadily** and resets at every kill is cause 2.

## Remediation

- **Cause 1: raise the memory limit** on the Deployment (not on the pod; a pod edit is lost at the
  next restart). Kaval's action `increase_memory_limit` does this: it **doubles** the limit, **never
  above 512 MiB**, asks for a person's approval first, and records the old value so it can be put back.
  Blast radius: that Deployment (its pods are replaced). Reversible: yes. At the ceiling the action
  refuses and a person decides.
- **Cause 2 (a leak): do not keep doubling.** Raise once to buy time, then fix the program. Open a
  ticket; the limit is not the fix
- **Cause 4: the node.** Free memory on the node or move work off it. Raising one container's limit
  makes it worse
- Afterwards, check the program stays up for several minutes and that memory sits comfortably under
  the new limit (below 90 %, or the pressure alert fires again)

## Do not

- **Do not restart the pod** as the fix for an OOM kill. It returns with the same limit and dies again
- Do not set a limit lower than the request, and do not remove the limit to "make it go away": an
  unlimited container takes the node's memory and everything else on the node suffers
- Do not raise a limit past what the node has free. Check `kubectl top node` first
- Do not keep approving doublings of the same program: the second one in an hour means a leak
