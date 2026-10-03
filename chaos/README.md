# Chaos experiments

Controlled failure injection, confined to a namespace labelled `kaval.io/chaos=enabled`.
Nothing here may touch anything outside it — blast-radius containment is tested before the
experiments are.

```bash
make chaos-run EXPERIMENT=oom-kill
```

## Planned (Phase 5)

| Experiment | Injects | Should produce |
|---|---|---|
| `oom-kill` | Memory limit breach | OOMKilled → limit patch proposal |
| `crashloop` | Bad command in a container | CrashLoopBackOff → rollback proposal |
| `disk-fill` | Volume filled to 95% | DiskPressure → cleanup proposal |
| `latency` | Injected network delay | SLO breach → investigation, no auto-action |
| `node-drain` | Simulated spot reclamation | Node lost → rebuild verification |

Each experiment is only complete when a matching runbook exists in `docs/runbooks/` — the
agent needs something to retrieve, and writing it is how you find out whether you actually
understand the failure.
