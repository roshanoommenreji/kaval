# Chaos experiments

Failures caused on purpose, **on staging only**, to prove Kaval notices, diagnoses and repairs them —
or, for some failures, that it correctly refuses to act and tells a person. Why our own small jobs
rather than Chaos Mesh or LitmusChaos, and how it is fenced in:
[ADR-0044](../docs/adr/0044-chaos-with-our-own-jobs-and-a-bounded-memory-fix.md).

```bash
make staging-up                 # ~5 min, about $0.045/hr
make chaos-run EXPERIMENT=oom   # switch on, cause the failure, follow it, switch off
make chaos-status               # the switch, the Jobs, the victim
make chaos-stop                 # the stop button
make mttr                       # how long each stage took
make staging-down
```

## How a game day goes

1. Copy [game-day-template.md](game-day-template.md) to `docs/postmortems/<date>-<experiment>.md` and
   write the **before** half: hypothesis, steady state, abort criteria. Before, not after.
2. `make chaos-run`. The switch turns on, a Job starts from the suspended CronJob, and the runner
   creates the victim in the demo namespace.
3. Kaval does its normal job. You approve the fix like any other.
4. The runner prints a timeline; `make mttr` splits the time by stage. The victim is deleted.
5. Fill in the **after** half the same day.

## What stops a run

- The **switch** (a ConfigMap that every deploy resets to off) — turn it off and the runner aborts and cleans up
- The **clock**: the runner stops itself at 12 minutes and the Job is killed at 15
- `make chaos-stop`, from anywhere
- **Prod renders none of this at all** (`chaos.enabled` is false there; a test and a CI step check it)

## The experiments

| Experiment | Injects | Should produce | Status |
|---|---|---|---|
| `oom` | A program needing ~96 MiB started with a 64 MiB limit | OOMKilled → proposal to raise the memory limit → approval → fixed and holding | Slice 3 (`KAV-79`) |
| `crashloop` | A bad command in a container | CrashLoopBackOff → rollback or fix proposal | Slice 4 (`KAV-80`) |
| `lost-server` | The node disappears | Node lost → rebuild verification | Slice 4 (`KAV-80`) |
| `disk-fill` | A volume filled to 95 % | DiskPressure → **alert to a person, no automated action** | Slice 5 (`KAV-81`) |
| `latency` | Injected slowness | SLO breach → investigation, **no automated action** | Slice 5 (`KAV-81`) |

Each experiment is only complete when a matching runbook exists in `docs/runbooks/` — the agent needs
something to retrieve, and writing it is how you find out whether you understand the failure. The
runner code is `services/executor/kaval_executor/chaos.py`; its chart objects are
`deploy/charts/kaval/templates/chaos.yaml`; the script behind the make targets is `scripts/ops/chaos.sh`.
