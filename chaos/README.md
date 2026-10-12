# Chaos experiments

Failures caused on purpose, **on staging only**, to prove Kaval notices, diagnoses and repairs them —
or, for some failures, that it correctly refuses to act and tells a person. Why our own small jobs
rather than Chaos Mesh or LitmusChaos, and how it is fenced in:
[ADR-0044](../docs/adr/0044-chaos-with-our-own-jobs-and-a-bounded-memory-fix.md).

```bash
make staging-up                 # ~5 min, about $0.045/hr
make chaos-run EXPERIMENT=oom   # switch on, cause the failure, follow it, switch off (or badrelease)
make chaos-node                 # lose the app server (laptop-run; asks you to type the machine id)
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
- The **clock**: the runner stops itself at 20 minutes (a person needs time to approve) and the Job is killed at 25
- `make chaos-stop`, from anywhere
- **Prod renders none of this at all** (`chaos.enabled` is false there; a test and a CI step check it)

## The experiments

| Experiment | Injects | Should produce | Status |
|---|---|---|---|
| `oom` | A program needing ~96 MiB started with a 64 MiB limit | OOMKilled → proposal to raise the memory limit → approval → fixed and holding | Slice 3 (`KAV-79`) |
| `badrelease` | A healthy program, then a new version that crashes at start-up | CrashLoopBackOff → proposal to **roll back** (not restart) → approval → previous version back and holding | Slice 4 (`KAV-80`, [ADR-0045](../docs/adr/0045-rolling-back-a-bad-release-and-flux-versus-direct-fixes.md)) |
| `node` (`make chaos-node`) | The app server is ended; the auto-scaling group replaces it | Nothing inside Kaval can notice (it ran on that machine); the run times the self-heal and checks no data was lost | Slice 4 (`KAV-80`, [ADR-0046](../docs/adr/0046-losing-the-app-server-and-how-we-test-it.md)) |
| `disk` | A program writes a bounded 3 GiB into a capped volume (staging's disk line is 0.38, so 30 % to 45 % crosses it) | `disk_pressure` incident of its own → **a person is told, no automated action**; a hand-filed restart is refused | Slice 5 (`KAV-81`, [ADR-0047](../docs/adr/0047-incidents-that-are-for-people-disk-full-and-slowness.md)) |
| `slow` | A web program that answers fast, then takes 3 s per answer | `latency_high` incident of its own → **a person is told, no automated action**; a hand-filed restart is refused | Slice 5 (`KAV-81`); read the result afterwards with `make chaos-check` |

Each experiment is only complete when a matching runbook exists in `docs/runbooks/` — the agent needs
something to retrieve, and writing it is how you find out whether you understand the failure. The
runner code is `services/executor/kaval_executor/chaos.py` (the `node` experiment is the laptop-run `scripts/ops/chaos_node.py`); its chart objects are
`deploy/charts/kaval/templates/chaos.yaml`; the script behind the make targets is `scripts/ops/chaos.sh`.
