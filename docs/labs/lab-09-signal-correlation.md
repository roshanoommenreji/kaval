# Lab 09 — Signal correlation: many signals, one incident

**Phase:** 2 · **Time:** ~30 min · **Cost:** about 1–2 cents of dev-server time

One failure produces a burst of signals. This lab turns that burst into **one incident**, with a
fingerprint that stays the same every time the failure comes back. Everything the agent does next
(finding similar past incidents, proposing a fix, measuring whether it worked) hangs off this.
The design is [ADR-0014](../adr/0014-signal-correlation-and-incident-fingerprints.md).

## Prerequisites

- [Lab 05](lab-05-gateway-api-and-synthetic-signals.md) done: `make signals` writes fake incidents
- `make dev` running, and `make dev-tunnel` in its own terminal

---

## Step 1 — Read the three rules

Open `services/agent/kaval_agent/correlate.py`. Grouping comes down to three questions, answered
by code and not by the model:

| Question | Answer for the OOM scenario |
|---|---|
| **What's broken?** (subject) | `kaval-demo/checkout`, the workload. The pod `checkout-bcdfg-hjklm` gets a new name on every restart, so the pod name can't be the subject |
| **What kind of broken?** (cause) | `oom_killed`. The restarts and the alert are symptoms; the most specific cause names the incident |
| **Is it over?** (time) | 15 quiet minutes with no new problem signal. Cost data is daily, so AWS waits 48 h |

Together they make the fingerprint `oom_killed:k8s:kaval-demo/checkout`.

**Why not let the AI group them?** Grouping is bookkeeping. It must be exact, fast and testable,
and a log line crafted by an attacker shouldn't be able to merge or split incidents.

## Step 2 — Unit tests first, no database needed

```bash
pytest services/agent -q
```

```
....................ss                                                   [100%]
20 passed, 2 skipped
```

The 20 are pure: each builds signals in memory and checks what one pass *would* write. The 2
skipped tests need Postgres. They run in CI, or here once the tunnel is up. Read one test, for
example `test_a_restarted_pod_keeps_its_fingerprint`. Each test states one rule.

## Step 3 — Migrate, write signals, correlate

```bash
make migrate                          # adds ix_incident_signal_signal_id
make signals SCENARIO=oom-crashloop
make correlate
```

```
opened    oom_killed:k8s:kaval-demo/checkout  high  6 signals
```

Run `make correlate` again:

```
nothing new
```

A second pass changes nothing, so the correlator is safe to run on a timer.

## Step 4 — See it through the API

```bash
curl -s localhost:8000/v1/incidents?status=open | python -m json.tool
```

Open one incident by id (`/v1/incidents/<id>`) and check that all six signals are listed under it.

## Step 5 — Cost: a normal day is not a problem

```bash
make signals SCENARIO=cost-spike
make correlate
```

```
opened    cost_spike:aws:ec2/ap-south-1  medium  1 signals
```

The scenario wrote seven days of cost. Six are normal and stay unlinked. Only the day above 3× the
week's median becomes an incident. The median is used rather than the mean, so one earlier spike
can't hide the next one.

## Step 6 — The lock (optional)

Two correlators running at once would open the same incident twice. Each pass takes a Postgres
**advisory lock** for the length of its transaction; a second pass that can't get it prints
`another correlator holds the lock; skipped this pass`. The test
`test_a_second_correlator_is_refused_while_one_runs` proves it with two real connections.

---

## Done when

- [x] `pytest services/agent` passes
- [x] `make signals SCENARIO=oom-crashloop` + `make correlate` opens one high incident with 6
      signals, and a second `make correlate` says `nothing new`
- [x] `/v1/incidents` shows it, with its signals
- [x] `cost-spike` opens one incident, for the spike day only
- [x] KAV-39 signed off: `jira-sync.py uat KAV-39 pass --env dev`

## What went wrong, and why (2026-09-28)

- **The "is this signal linked yet?" query had no index.** `incident_signal`'s primary key is
  `(incident_id, signal_id)`. An index is used from its first column, so it can't look up by
  `signal_id` alone, and every pass would have scanned the table. The migration adds
  `ix_incident_signal_signal_id`.
- **The idle-volume test was first written wrong: a timing slip, not a logic one.** The scenario's
  one signal is stamped "now", and a new group waits 60 s before it opens, so a pass in the same
  second opens nothing. That's the intended behaviour, and the test now runs its pass two minutes
  later. The 60 s wait catches people out, so that's where to look first if `make correlate` says
  `waiting`.
- **UAT hit the dev server's idle-stop mid-session** (ADR-0007): the SSH tunnel and `docker
  --context kaval-devbox` both failed with connection errors partway through. `make devbox-up`
  brought it back in about a minute. The accidental pause turned out to be a useful extra check:
  the next `make correlate` correctly closed an incident that had gone quiet purely on wall-clock
  time, with no pass having run while it happened — proof the quiet window isn't counting passes.
