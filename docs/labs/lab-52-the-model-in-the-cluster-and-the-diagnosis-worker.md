# Lab 52 — The model in the cluster, and a worker that diagnoses new incidents by itself

**Phase:** 5 · **Story:** `KAV-87` · **Time:** about 3 hours, half of it the staging proof · **Cost:** the dev server for the local checks (about $0.027 an hour); staging while it is up (about $0.045 an hour); nothing recurring (see the cost section of the ADR).

Until this lab, nothing running in a cluster ever called the language model. A person ran `make diagnose` from a laptop. This
lab puts the model on the app node as its own pod, adds a separate worker that picks up every new incident and files
the proposal, and watches the worker so a failure is noticed. The decision is
[ADR-0055](../adr/0055-the-model-runs-in-the-cluster-and-diagnoses-new-incidents.md).

## Prerequisites

- [Lab 51](lab-51-teaching-the-model-the-fixes.md) done (the model is shown one fix per cause)
- `helm` and Docker installed on the laptop (the chart tests and the alert-rule tests use them)
- For Step 4: the dev server up (`make dev`), `make dev-tunnel` running in its own terminal

## Step 1 — The worker's unit tests

```bash
pytest services/agent/tests/test_diagnoser.py -q        # 22 tests; the database ones skip with no database
```

Read these first, they are the rules:

- `test_a_new_incident_gets_a_proposal_without_anyone_asking` and `test_an_incident_is_never_diagnosed_twice`:
  one proposal per incident, and a second pass does nothing.
- `test_an_incident_for_a_person_is_never_sent_to_the_model`: Kaval's own health, a full disk and a slow service
  are skipped. `test_a_closed_incident_is_left_alone` and `test_an_incident_older_than_the_limit_is_left_for_a_person`.
- `test_a_model_failure_writes_nothing_and_leaves_the_incident_waiting` and `test_during_the_pause_the_model_is_not_asked`:
  the failure path, and that the model is not hammered while it recovers.
- `test_a_bug_in_our_own_code_is_not_hidden_as_a_model_failure`: only the model's own failures are caught; anything
  else crashes loudly.
- `test_over_the_hourly_limit_incidents_wait_and_the_model_is_not_asked` and `test_a_second_copy_does_nothing_while_the_first_holds_the_lock`.
- `test_the_outcome_label_only_ever_takes_a_fixed_handful_of_words`: the metrics page is readable by every pod.

## Step 2 — The chart

```bash
pytest scripts/dev/test_chart_diagnoser.py scripts/dev/test_gitops_values_in_sync.py -q
helm template kaval-staging deploy/charts/kaval -f deploy/environments/staging/values.yaml | grep -n "kind: Deployment" -A3
```

What the tests pin: prod and local render **none** of it; neither new pod gets a ServiceAccount token; the model has
a 1.5 GiB memory limit, `Recreate`, a read-only filesystem, no root, no capabilities; the image is pinned by
digest and the model ids are pinned; the model's Service is a plain `ClusterIP`; the worker uses the **agent's**
database role and no stronger one; the worker is scraped and its alert rules are loaded; the chart **refuses to
render** the worker without the model; and switching the worker off removes its alert rules too (an `absent()` rule
for something that is not meant to exist would page forever).

The staging file and the Flux copy (`deploy/gitops/staging/helmrelease.yaml`) are kept in step by a test.

## Step 3 — The alert rules, with their quiet cases

```bash
make rules-test        # Docker; uses the exact Prometheus image the chart pins
```

`kaval-diagnoser.test.yaml` checks four things: nothing waiting is quiet; a normal 5-minute wait never alerts; a
15-minute wait alerts after the 2-minute hold, with the labels the poller reads; and a worker with **no pod at
all** alerts (a dead pod's numbers vanish with it, so the rule asks for the missing series).

## Step 4 — The worker against the real model (the dev server)

```bash
make dev && make dev-tunnel            # the tunnel in its own terminal
make signals SCENARIO=oom-crashloop    # one fake incident (or: python -m kaval_collector.synthetic oom-crashloop)
python -m kaval_agent.correlate        # wait 60 s first: a group opens 60 s after its first signal
make diagnoser-once                    # the worker's single pass
make diagnoser-once                    # and again
```

Result (2026-10-14): the first pass indexed the runbooks shipped for the model (49 new, 2 changed, 22 unchanged) and
then `ok <incident> (0 still waiting) proposal <id>, 1 action(s)`: `increase_memory_limit` aimed at
`kaval-demo/checkout`, class `ask`, written by `gemma3:1b-it-qat`, 58 seconds in all. The second pass printed
`nothing waiting` (6 seconds): the incident has a proposal, so it is not asked again. Running with `--every 5` serves
`:9100` with `kaval_diagnosis_waiting`, `kaval_diagnosis_oldest_wait_seconds`, `kaval_diagnosis_last_seconds` and the
loop numbers, and nothing that comes from data.

The model's start-up script was also run as the pod runs it (non-root, read-only filesystem, no capabilities) on the
dev server: with the right pinned id it prints `ok` for both models and exits 0; with a wrong id it prints
`REFUSING: all-minilm has id '1b226e2802db', the pinned id is 'deadbeef0000'` and exits 1.

Delete the fake incident afterwards if you want a clean database (the tests of other services read all the rows).

## Step 5 — Staging, the live proof

Done after the merge, because Flux deploys what is on `main` and the new image is pinned by the release pull request.
Written up under "What happened on staging" below once it has run. The proofs it must show:

1. node memory with the model idle and loaded (`kubectl top node`), the model pod's real memory, time per diagnosis;
2. a new incident (the out-of-memory game day) gets a proposal with nobody running `make chaos-propose` by hand, and
   is **not** diagnosed again on the next pass;
3. a full-disk incident (for a person) gets no proposal from the worker;
4. the model scaled to zero: the incident stays open, `KavalDiagnosisBacklog` fires after 15 minutes, the correlator
   and the executor keep working;
5. the model back: the backlog drains and each incident has exactly one proposal.

## What happened on staging

Run on 2026-10-15 (build `sha-b36c4be`, then the fix of PR #170). Numbers and times are in the ADR's
[Measured](../adr/0055-the-model-runs-in-the-cluster-and-diagnoses-new-incidents.md) table; the full record of the
game days is [the post-mortem](../postmortems/2026-10-15-oom-diagnosed-by-itself.md). How to repeat it:

```bash
AWS_PROFILE=kaval make staging-up           # answer y; ~5 min. Both new pods Running on the new image
make staging-status                         # or read the pods over Session Manager (Step 5 of the runbook)
ASSUME_YES=1 AWS_PROFILE=kaval make chaos-run EXPERIMENT=oom    # the failure; leave the terminal alone
make chaos-propose EXPERIMENT=oom           # finds the proposal the WORKER filed; prints ACTION <id>
make chaos-approve ACTION=<id>              # inside staging's 15-minute approval window
make chaos-mttr                             # time to restore, once the executor's 5-minute check has run
```

What it proved:

1. **Both pods came up on the new image.** The worker started before the model had finished downloading, said
   `runbooks: not synced yet` three times, then indexed 78 runbook chunks by itself: starting early is harmless.
2. **A new incident got exactly one proposal, with nobody running a command**, about 2 min 35 s after the failure
   started (60 s of that is the correlator's grouping wait). The passes after it printed `nothing waiting`.
3. **A full-disk incident got none** (`make chaos-check` passed: a person is told, nothing is proposed or run).
4. **Model off** (`kubectl scale deploy/<release>-ollama --replicas=0` over Session Manager): the incident stayed
   open, the worker counted `model_error` and paused, `KavalDiagnosisBacklog` fired, the poller opened
   `kaval_diagnosis_backlog`, and the correlator and executor carried on.
5. **Model back on:** ready in 35 s, the waiting incident got its one proposal at the worker's next try, the alarm
   cleared. In a second, clean run the proposal was approved 6 s after it appeared, the limit was raised 200 s after
   the failure began and the program stayed up.
6. **Memory and cost:** the model adds about 1.13 GB while loaded and gives it back 5 minutes after the last use;
   about 780 MB stayed available on staging at the worst moment; no extra processor credits were charged.

Three things went wrong, and are why this section is worth reading:

- **The worker crashed on the second incident**: its database account could not read `outcome`. The laptop uses an
  administrator account, so the laptop never showed it; only a real role on a real cluster did. Fixed by one
  read-only grant and a test that names every table the worker reads (PR #170). `db-roles.sql` is re-applied from
  `main` by the node every 6 hours; to apply it at once, start `kaval-gitops-bootstrap.service` on the node.
- **An approval expired**: staging gives 15 minutes and I took 17. The worker did not make a second proposal (by
  design); `CHAOS_BY_HAND=1 make chaos-propose` files a new one, which exposed that the switch never reached the pod
  (fixed, PR #170).
- **I edited `chaos.sh` while a game day was running it.** Bash reads a script as it runs, so the running copy hit a
  syntax error and cleaned up early. Never edit a script that is running; the repair was a re-run.

## Done when

- [x] `pytest services/agent/tests/test_diagnoser.py scripts/dev/test_chart_diagnoser.py scripts/ops/test_chaos_propose.py -q` passes
- [x] `make rules-test` passes (both rule files)
- [x] `make lint` shows no new errors (3 old ones in test files are about how this laptop imports the services, not about this change)
- [x] The worker diagnosed a fake incident with the real model and did not diagnose it twice
- [x] The staging proofs above (2026-10-15)
- [ ] Production: model memory and cost measured on the real node, then promoted

## Clean-up

`make devbox-down`. Staging parks itself after four idle hours, or `make staging-down`.
