# Lab 50 — Closing Phase 5: readiness checks, the time-to-restore chart, and Prometheus in production

**Phase:** 5, slice 7 · **Cost:** Part 1 about $0 (tests only) plus about 30 minutes of staging (~$0.03);
the production roll-out is a few hours of production (about 5 cents an hour) and nothing recurring

Slices 1 to 6 proved Kaval on staging. This slice closes the phase: the last open findings, a chart of
what the game days measured, and then, only on your say-so, production. It grows in three parts, one
pull request each; the parts built so far are below.

Decisions: [ADR-0051](../adr/0051-closing-phase-5-readiness-checks-prod-prometheus-and-the-disk-trend.md).

## Prerequisites

- [Lab 49](lab-49-people-and-process.md) done; `helm` and Docker installed (chart render, promtool)
- `AWS_PROFILE=kaval` for the staging proof

---

## Part 1 — readiness checks, the chart, production's settings

### Step 1 — the checks, tested by rendering the real chart

```bash
pytest scripts/dev/test_chart_probes.py -q
helm template k deploy/charts/kaval -f deploy/environments/prod/values.yaml | grep -B2 -A6 "readinessProbe"
```

Each of the agent, executor, collector and the Prometheus poller has a check that connects to its
health page (`:9100`, named `metrics`). Read the negative tests: no liveness check is allowed on them
(a restart because the database is slow makes a bad hour worse), and the same checks must render in
staging, production and local. To see a test fail for the right reason, delete the one line
`{{- include "kaval.workerReadiness" . | nindent 10 }}` from `templates/agent.yaml` and run it again:
four tests fail. Put the line back.

### Step 2 — production's settings are the written settings

```bash
pytest scripts/dev/test_gitops_values_in_sync.py scripts/dev/test_chart_chaos.py -q
```

Production now has `prometheus.enabled`, `prometheus.database.enabled` and one timed service (its own
gateway) in both copies of its settings, and the test that keeps the copies equal passes. The prod
render still holds no chaos object, and the disk line is the chart's `0.85`.

### Step 3 — the chart of what the game days measured

```bash
python scripts/dev/mttr_chart.py            # redraws docs/architecture/mttr.svg
pytest scripts/dev/test_mttr_chart.py -q
```

![time to restore by stage](../architecture/mttr.svg)

The numbers are copied from each post-mortem into `docs/architecture/mttr-runs.json` (staging takes its
database, and `make mttr`, with it when it is destroyed). Change one total by a second and the test
says which run no longer adds up. Reading it: the person's wait is the orange bar and, in the first oom
run, 63 percent of the whole; the machine's own time is the same size in every fix (about two and a half
minutes); a lost server heals with nobody acting in about two minutes ten.

### Step 4 — the staging proof: a release that dies a few seconds after it starts

```bash
AWS_PROFILE=kaval make staging-up           # answers its prompt with ASSUME_YES=1 when scripted
# on the node (Session Manager), for the agent, once "with" and once "without" the check:
bash badrelease.sh with       # patch the Deployment's command to: sleep 20; exit 1   (a program that starts, then dies)
bash badrelease.sh without    # the same, with the readiness check removed in the same patch
```

The "release" is a command override that runs for 20 seconds and exits, which is the nasty case: the
container looks healthy for a moment. Run on 2026-10-13, staging on `sha-e13d854`, chart from `main`:

| | With the readiness check | Without it (control) |
|---|---|---|
| 10 s after the release | old pod `1/1 Running`, new pod `0/1` | new pod `1/1 Running`, **old pod `Terminating`** |
| 40 s | old `1/1 Running`, new `0/1` (restarting) | **old pod gone**; only the new pod left |
| 80 s | old `1/1 Running`; new `0/1 Error`, 2 restarts | only the new pod, `0/1 Error`: **nothing serving** |
| `kubectl rollout status` | times out (stuck, not an outage) | times out |
| `kubectl rollout undo` | rolled out; old pod never left | rolled out; a new good pod had to start |

So the check does what the game day said it would: the working copy keeps working while a bad release
sits at `0/1`. Without it the good copy was removed within 10 to 40 seconds of the release. Both runs
were undone with `rollout undo` and ended with one good pod, `restarts=0`.

Everything else on the node, with all six pods ready and the checks on: **1,407 MiB used, 2,231 MiB of
3,829 MiB available**; pods 39 to 81 MiB each (Prometheus 39 MiB six minutes after it started; 86 MiB
was its figure after a long run in Lab 45). This is the steady state with chaos installed but switched
off. Production's own number is taken when production runs (Part 3).

## Done when

- [x] Readiness checks on the four background programs, rendered in every environment (tests)
- [x] Production's settings turn Prometheus, the database exporters and a gateway check on (tests)
- [x] The time-to-restore chart and its arithmetic test
- [x] The disk-trend decision recorded with its trigger (ADR-0051)
- [x] The staging proof of Step 4 (2026-10-13): the old copy kept serving with the check; was removed within 40 s without it
