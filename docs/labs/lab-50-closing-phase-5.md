# Lab 50 — Closing Phase 5: readiness checks, the time-to-restore chart, the outside check, and Prometheus in production

**Phase:** 5, slice 7 · **Cost:** Part 1 about $0 (tests only) plus about 30 minutes of staging (~$0.03);
Part 2 $0 recurring (a free monitoring service, a free IAM role, a free GitHub job) plus an hour of
staging; the production roll-out is a few hours of production (about 5 cents an hour) and nothing recurring

Slices 1 to 6 proved Kaval on staging. This slice closes the phase: the last open findings, a chart of
what the game days measured, and then, only on your say-so, production. It grows in three parts, one
pull request each; the parts built so far are below.

Decisions: [ADR-0051](../adr/0051-closing-phase-5-readiness-checks-prod-prometheus-and-the-disk-trend.md)
(Part 1 and the roll-out order), [ADR-0052](../adr/0052-the-outside-still-alive-check.md) (Part 2).

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

## Part 2 — the outside "still alive" check

Everything Kaval does to notice trouble runs inside the system it watches. If the server is gone,
nothing inside can say so. The fix is a small outside service that expects a ping every few minutes
and e-mails you when the pings **stop** ([ADR-0052](../adr/0052-the-outside-still-alive-check.md)).

### Step 1 — the pieces, tested

```bash
pytest services/collector/tests/test_heartbeat.py -q               # the ping: throttle, retry, no redirect, never raises
pytest scripts/ops/test_still_alive.py -q                          # the parked cover: when it pings, and when it must not
pytest scripts/dev/test_chart_heartbeat.py scripts/dev/test_node_heartbeat_secret.py -q   # the Secret wiring
```

Read the negative tests first: an `http://` address is refused, a redirect is not followed, a timeout
or a refused connection is a plain reason and never an exception, the address never appears in any
message, a poller whose Prometheus did not answer sends nothing, and the cover pings only for a
group that **wants 0 servers and has none** (anything unclear is "unknown" and sends nothing). The
node-start-up test runs the real step with a pretend `aws` that fails: the script carries on and the
Secret is empty. To see one fail for the right reason, change `if HB_VALUE=$(aws ...); then` in
`infra/modules/node/user_data.sh.tftpl` to a bare assignment: the two failing-`aws` cases go red.

### Step 2 — make the checks on the service (you, once, about 5 minutes)

1. Sign up at healthchecks.io (free tier: 20 checks; Kaval uses two).
2. Create a check named **kaval-prod**: schedule **Simple**, period **10 minutes**, grace **15 minutes**.
3. Create a second check named **kaval-staging**: for the proof, the smallest period and grace the page
   allows (a few minutes), so the alarm arrives while you watch. Raise it afterwards.
4. Check that the e-mail integration is on for both (it is by default). Slack is deliberately not used.
5. Do not create an API key. Kaval never needs one (ADR-0052, Decision 4).
6. Each check shows a ping address. **Do not paste it into chat.** Store each with the script below.

```bash
AWS_PROFILE=kaval scripts/ops/set-heartbeat.sh staging    # hidden prompt; staging check's address
AWS_PROFILE=kaval scripts/ops/set-heartbeat.sh prod       # later, in Part 3: also needs `gh auth login`
```

### Step 3 — the staging proof (an image release, then a real stop)

The poller's new code ships in the collector image, so it goes through the normal release path first:
merge the pull request, merge the release pull request that pins staging's new tag, then
`make staging-up`. Terraform for staging adds the parameter and one IAM line; apply it only after
reading the plan. The running server was started before this change and does not have the new
start-up step, so end it once and let the group replace it (the same move as the lost-server game
day), or `make staging-down` and `staging-up`.

1. Store the staging address with `set-heartbeat.sh staging` **before** the server (re)starts.
2. On the node, once the pods are ready: the poller's log shows `heartbeat: first ping sent`:
   `kubectl logs -n kaval-staging deploy/kaval-staging-collector-prometheus | grep heartbeat`.
3. On the service, **kaval-staging** turns green ("up") and its log shows a ping about every 2 minutes.
4. The negative proof, the point of the whole feature: stop the poller on staging
   (`kubectl scale deploy/kaval-staging-collector-prometheus --replicas=0 -n kaval-staging`), start a
   stopwatch, and wait. Expect the check to go late, then **down**, then an e-mail. Note the minutes.
5. Scale it back to 1: the check returns to up, and the poller logs nothing new (recovery is silent on its
   side; the service sends its own "up" e-mail).
6. Check the other negative: with the Secret deleted
   (`kubectl delete secret kaval-heartbeat -n kaval-staging`) and the poller restarted, the poller still
   starts and does all its other work, and prints no ping lines (the reference is optional).

| | Result |
|---|---|
| First ping from the poller | (fill in from the run) |
| Stopped poller -> down e-mail | (fill in: minutes) |
| Recovery after scaling back | (fill in) |
| Poller memory with the ping on | (fill in from the metrics page) |

### Step 4 — production (Part 3)

Nothing here touches production. In Part 3, after the production plan is read and approved: apply
(the `kaval-still-alive` role, the parameter), store the address with `set-heartbeat.sh prod`, set the
repository variable `AWS_STILL_ALIVE_ROLE_ARN` (the output `still_alive_role_arn`), and run the
`still-alive` workflow once by hand. Production is parked, so the run pings for it and **kaval-prod**
turns green. That is also how you know the cover works. Then bring production up and watch the poller's
pings take over, the workflow going quiet.

## Done when

- [x] Readiness checks on the four background programs, rendered in every environment (tests)
- [x] Production's settings turn Prometheus, the database exporters and a gateway check on (tests)
- [x] The time-to-restore chart and its arithmetic test
- [x] The disk-trend decision recorded with its trigger (ADR-0051)
- [x] The staging proof of Step 4 (2026-10-13): the old copy kept serving with the check; was removed within 40 s without it
- [x] Part 2 built and tested: the ping, the parked cover, the role, the Secret wiring (ADR-0052)
- [ ] Part 2 proven live on staging (Step 3 table filled in)
