# Lab 50 — Closing Phase 5: readiness checks, the time-to-restore chart, the still-alive alarm, and Prometheus in production

**Phase:** 5, slice 7 · **Cost:** Part 1 about $0 (tests only) plus about 30 minutes of staging (~$0.03);
Part 2 about $0.72 a month at most (probably $0) plus about an hour of staging; the production roll-out is a few
hours of production (about 5 cents an hour) and nothing else recurring

Slices 1 to 6 proved Kaval on staging. This slice closes the phase: the last open findings, a chart of
what the game days measured, and then, only on your say-so, production. It grows in three parts, one
pull request each; the parts built so far are below.

Decisions: [ADR-0051](../adr/0051-closing-phase-5-readiness-checks-prod-prometheus-and-the-disk-trend.md)
(Part 1 and the roll-out order), [ADR-0053](../adr/0053-the-still-alive-alarm-lives-in-aws.md) (Part 2;
it replaced [ADR-0052](../adr/0052-the-outside-still-alive-check.md), which was built and merged first).

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

## Part 2 — the "still alive" alarm

Everything Kaval does to notice trouble runs inside the system it watches. If the server is gone, nothing
inside can say so. The fix is an alarm that is not inside the server: the node puts a small "still alive"
datapoint into CloudWatch every two minutes, and one alarm e-mails you when they stop while the server is
meant to be running ([ADR-0053](../adr/0053-the-still-alive-alarm-lives-in-aws.md)).

### Step 1 — the pieces, tested

```bash
pytest scripts/dev/test_node_heartbeat.py -q       # the node script: every unclear case sends nothing
pytest scripts/dev/test_still_alive_alarm.py -q    # the alarm's rule, fills, 5 x 5 minutes, both e-mails, the fence
terraform -chdir=infra/envs/staging validate && terraform fmt -check -recursive infra
```

Read the negative tests first: the poller's page unreachable, no finish time, a stale one, one from the future,
`NaN`, or another service's fresh line must all send nothing. To see two fail for the right reason, change `-gt 150`
in the script to `-gt 150000`, or delete `&& $1 ~ /collector-prometheus/`, and run the file again.

### Step 2 — try the rule against real data before trusting it (read-only)

The rule is a CloudWatch expression, so its odd cases were tried against real, empty data first (a query only;
nothing is created):

```bash
# m1 = a heartbeat metric that has never existed; m2 = a real series. Expression:
#   IF(FILL(m2, 1) > 0 AND FILL(m1, 0) < 1, 1, 0)
aws cloudwatch get-metric-data --metric-data-queries file://q.json --start-time ... --end-time ...
```

| Group size (m2) | Heartbeat (m1) | Result | Meaning |
|---|---|---|---|
| a real series above 0 | never existed | 1, every period | wants a server and silent: rings |
| a real series of 0 | never existed | 0, every period | parked: quiet |
| missing entirely | missing entirely | 1, every period | no reading at all: leans toward ringing |

(Run 2026-10-13 with `AWS/EC2` series standing in for the group size.)

### Step 3 — the staging proof

```bash
cd infra/envs/staging && terraform plan -out=p && terraform apply p   # read the plan first
```

The running server was started before this change and does not have the new script, so end it once and let
the group replace it (the same move as the lost-server game day). Staging's alarm rings after 10 minutes of
silence, not 25 (`silent_periods = 2`: same rule, only the number differs). Then:

1. Datapoints arrive: `aws cloudwatch get-metric-statistics --namespace Kaval/Heartbeat --metric-name StillAlive --dimensions Name=Environment,Value=kaval-staging --start-time <15 min ago> --end-time <now> --period 120 --statistics Sum --profile kaval`
2. The group's size metric exists: the same with `--namespace AWS/AutoScaling --metric-name GroupDesiredCapacity --dimensions Name=AutoScalingGroupName,Value=kaval-staging --statistics Maximum`
3. The alarm is quiet: `aws cloudwatch describe-alarms --alarm-names kaval-staging-still-alive --query 'MetricAlarms[0].StateValue'` says `OK`
4. The point of the whole feature: stop the poller (`kubectl scale deploy/kaval-staging-collector-prometheus --replicas=0 -n kaval-staging`), start a stopwatch. Expect `ALARM` about 10 to 15 minutes later
5. Scale it back to 1: the datapoints return and the alarm goes back to `OK`
6. The other negative, a parked server stays quiet: park staging (`aws autoscaling set-desired-capacity --auto-scaling-group-name kaval-staging --desired-capacity 0 --profile kaval`), wait more than 10 minutes with no datapoints, and confirm the alarm is **not** in `ALARM` and the group-size metric shows 0

What actually happened (staging, 2026-10-13 to 14; alarm history and metrics read back from CloudWatch). The
replaced server launched at 18:48:38 UTC. The planned stop-the-poller test was overtaken by a real stop, below,
which exercised the same rule.

| | Result |
|---|---|
| First datapoint after the replaced server booted | 18:50 bucket; the alarm went `ALARM` to `OK` at 18:53 (it started in `ALARM` because the old server, which had no script, had been silent) |
| Alarm while datapoints arrive | `OK` from 18:53 to 20:43, with 2 to 3 datapoints in every 5-minute reading (the node sends every 2 minutes) |
| The poller stopped finishing passes -> ALARM | **`ALARM` at 20:43:17**, about 10 minutes after the last full reading, as set for staging (see "Found live") |
| Datapoints return -> back to OK | `OK` at 21:47 after a datapoint at 21:45; two more brief rings and returns followed, with only single datapoints at 21:45 and 22:00 |
| Parked staging with no datapoints | Staging was parked at 23:01 (the group's size metric read **0** from 23:00). The alarm went to `OK` at 23:06 and **stayed `OK` for 5.5 hours** with no datapoint at all, which is the parked-is-quiet case, checked against real data |

**Found live: the nightly stop took staging's database but not its server.** At 20:30:08 UTC (02:00 IST) the
budget guard's nightly rule (`kaval-budget-hard-stop`, "stop every running `Project=kaval` server outside an
auto-scaling group", [ADR-0028](../adr/0028-prod-app-node-on-demand-and-ceiling-50.md) and the budget module)
stopped staging's database server ("User initiated (2026-10-13 20:30:08 GMT)"; its Lambda log reads
`stop_instances=True tag=Project=kaval`). It scales only `kaval-prod`'s group, so staging's server kept running with
no database. The poller could no longer finish passes (the server's outbound traffic fell by about 60 percent at
20:30), the datapoints stopped, and the alarm rang: a real failure caught from outside, which is what it is for.
This is an old quirk, not new: it only shows when staging is left up across 02:00 IST, and staging parks itself
after four idle hours. Not changed here; the options (tag-exempt staging's database from the nightly rule, or
accept it) are recorded in the journal. For production the same rule scales the group to 0 and stops the
database together, so the alarm reads "parked" and stays quiet.

### Step 4 — production (Part 3)

Nothing here touches production. In Part 3, after the production plan is read and approved: apply (the alarm,
the topic and your e-mail subscription, the node's permission, the group metric), **click the confirmation link
AWS e-mails you**, then test the whole chain without waiting: `aws cloudwatch set-alarm-state ... --state-value
ALARM`, see the e-mail, then `--state-value OK`. Production is parked, so the alarm stays quiet until it is
brought up, and then the datapoints begin about 3 minutes after the server boots.

## Done when

- [x] Readiness checks on the four background programs, rendered in every environment (tests)
- [x] Production's settings turn Prometheus, the database exporters and a gateway check on (tests)
- [x] The time-to-restore chart and its arithmetic test
- [x] The disk-trend decision recorded with its trigger (ADR-0051)
- [x] The staging proof of Step 4 (2026-10-13): the old copy kept serving with the check; was removed within 40 s without it
- [x] Part 2 built and tested: the node script, the alarm, the permission, the group metric (ADR-0053)
- [x] Part 2 proven live on staging (2026-10-13): datapoints arrive, the alarm goes OK, rings when the poller cannot finish passes, and stays OK through 5.5 hours of parked staging. Still to do in Part 3: the production apply and the real e-mail test
