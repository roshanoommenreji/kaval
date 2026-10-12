# Lab 48 — Chaos: a full disk and a slow service, where the right answer is "tell a person"

**Phase:** 5, slice 5 · **Time:** ~4 h (build ~2 h, staging game days and the two things they found ~2 h) · **Cost:** about $0.20 (staging for the session; nothing recurring)

Slices 3 and 4 checked that Kaval *fixes* a failure. Two everyday failures have no fix a program should
apply: **a disk that is filling** and **a service that has become slow**. A restart frees no disk and
speeds nothing up. The right outcome is that Kaval notices, **tells a person, and proposes nothing**, and
this lab builds that and then proves it by breaking things on purpose. The decisions are in
[ADR-0047](../adr/0047-incidents-that-are-for-people-disk-full-and-slowness.md); the write-ups are
[the slow service](../postmortems/2026-10-12-slow.md) and [the disk](../postmortems/2026-10-12-disk.md); the
runbooks are [`disk-pressure.md`](../runbooks/disk-pressure.md) and
[`latency-spike.md`](../runbooks/latency-spike.md).

## Prerequisites

- [Lab 47](lab-47-chaos-bad-release-and-lost-server.md) done (the runner, the switch, `make chaos-*`)
- `AWS_PROFILE=kaval`; Docker running (for the policy tests); the new build on staging (check
  `make staging-status`)

---

## Step 1 — the rule: some causes are for people, enforced four times

```bash
pytest services/shared/tests/test_people_only.py services/agent/tests/test_diagnose.py \
       services/executor/tests/test_executor_selection.py -q
docker run --rm -v "$(pwd -W)/policy:/policy" openpolicyagent/opa:1.21.0 test /policy   # 23/23
```

`disk_pressure`, `latency_high` and every cause starting `kaval_` are for a person. The agent writes no
proposal, the policy marks any action attached to one `never` (the incident's cause is now policy input),
the executor asks the policy again from the action's own incident, and the gateway already refuses to
approve a `never` action. A test fails if the Python list and the Rego list ever differ.

## Step 2 — measure first: can the disk be seen already?

```bash
AWS_PROFILE=kaval make staging-up
AWS_PROFILE=kaval make staging-measure-disk     # read-only
```

On staging, cAdvisor (which Prometheus already scrapes) reports the root disk's used and total bytes:
6.10 GB of 21.40 GB (28.5 %) against `df`'s 29 %. That settled it: no new agent, just two more kept
metrics and one query (in the chart's `prometheus.disk.query`).

## Step 3 — the two checks, tested without a cluster

```bash
pytest services/collector/tests/test_probe.py services/collector/tests/test_prometheus.py -q
```

Read the negative tests first: disk at 70 % stays silent, a fast service stays silent, one slow answer
stays silent, a refused connection is *down*, not *slow*, a value that is not a fraction (NaN, infinity,
85 meant as a percentage) is not a full disk, and a redirect is not followed. The slowness check lives in
`kaval_collector.probe` and runs inside the poller that already existed. Its addresses come only from
the deployment's settings.

## Step 4 — the Slack message, straight from the signals

```bash
pytest services/gateway/tests/test_health_alerts.py -q
```

Both problems reuse the path built for Kaval's own health: one message per fault per half hour, a fixed
sentence, no number or text from the signal copied in, the runbook link, no buttons.

## Step 5 — the correlator's lane for people-only problems

```bash
pytest services/agent/tests/test_correlate.py -q
```

This step exists because the first slow-service game day found a hole: see "What went wrong". People-only
problems always form their own incident under their own name; ordinary signals never join one.

## Step 6 — the same settings in both places

```bash
pytest scripts/dev/test_gitops_values_in_sync.py scripts/dev/test_chart_chaos.py -q
```

Flux deploys the inline `values:` of `deploy/gitops/<env>/helmrelease.yaml`, a hand-kept copy of
`deploy/environments/<env>/values.yaml`. The first run of the slow-service game day found nothing
because a setting was only in one of them. The first test fails whenever the two differ; the second
checks that prod still gets the disk line 0.85 and probes nothing, that staging's line is 0.38, and that
the runner can create a Service but delete only its own.

## Step 7 — game day: a slow service

Copy [the template](../../chaos/game-day-template.md) to `docs/postmortems/<date>-slow.md` and write the
**before** half. Then:

```bash
AWS_PROFILE=kaval make chaos-run EXPERIMENT=slow        # terminal 1, about 14 minutes
AWS_PROFILE=kaval make chaos-propose EXPERIMENT=slow    # terminal 2, about 7 minutes in (while it is still running)
AWS_PROFILE=kaval make chaos-approve ACTION=<the code it printed>
AWS_PROFILE=kaval make chaos-check                      # when terminal 1 finishes
```

You should see: the incident `latency_high:...` (medium); `policy class: never` on the hand-filed restart;
**HTTP 409** on approving it; and `chaos-check` ending `PASS` with the exact message the real alert code
would post. Staging has no Slack, so the check runs that code as a dry run over the real signals, inside
a transaction it rolls back.

## Step 8 — game day: a disk filling

```bash
AWS_PROFILE=kaval make chaos-run EXPERIMENT=disk        # writes a bounded 3 GiB; staging's disk line is 0.38
AWS_PROFILE=kaval make staging-measure-disk             # about 3 minutes in: the disk should read about 45 %
AWS_PROFILE=kaval make chaos-propose EXPERIMENT=disk
AWS_PROFILE=kaval make chaos-approve ACTION=<the code it printed>     # expect 409
AWS_PROFILE=kaval make chaos-check
AWS_PROFILE=kaval make staging-measure-disk             # after: back to about 30 %
```

The program writes into a volume capped at 4 GiB, in a container capped at 4 GiB, with nothing on a host
path, and everything it wrote is gone when it is deleted.

## Step 9 — tear down

`AWS_PROFILE=kaval make staging-down`. Then finish each post-mortem's **after** half the same day.

---

## What went wrong, in the order it happened

1. **The first slow-service run found nothing.** The poller's probe target was empty on the cluster: the
   setting was added to `deploy/environments/staging/values.yaml` but staging deploys the copy inside
   its HelmRelease. Every render and every test was green. Fixed, and a test now compares the two.
2. **My fix commit failed and I did not see it.** The subject was six characters over the limit, I had
   filtered the output that said so, and a pull request merged without the fix it described. The cluster
   showed it (the setting still empty). Check that a commit exists after making it.
3. **The second run found a safety hole.** The slowness signals were written, but they were attached
   to an older incident named `unhealthy` (a harmless "readiness check failed while starting" event had
   opened it seconds before). The rule "no fix for a slow service" reads the incident's name, so the
   slow service would still have been allowed a restart proposal. Fixed with the correlator lane, and
   five tests that reproduce the live case.
4. **The new poller crashed for about five minutes after the first deploy**: the chart (read from git at
   once) reached the cluster before the new image (a separate pull request), so the old image got new
   arguments. It healed by itself, and Kaval's own "service is down" alert fired for it, which is that
   alert working on a real fault.

Tests with a fake cluster could not have found 1 or 3; only running it could. That is the argument for
game days, again.

## Done when

- [x] `disk_pressure` and `latency_high` are for a person: no automatic proposal, the policy says `never`, the executor re-checks, the gateway refuses approval (tests; the first and last live)
- [x] Disk and slowness signals collected and reach an incident of their own (live, both game days)
- [x] Disk at 70 % stays silent; a fast service stays silent; a broken query is not a full disk (tests; the disk at 30 % against the 38 % line live)
- [x] Both game days end with an escalation and zero automated action proposed (`make chaos-check` PASS, slow and disk)
- [x] Two post-mortems the same day, two runbooks, one ADR, and a test that keeps an environment's two settings copies equal
