# Lab 46 — Chaos: break one thing on purpose and watch Kaval fix it (out of memory)

**Phase:** 5, slice 3 · **Time:** ~2 h (build ~1 h, one staging game day ~30 min) · **Cost:** about $0.10 (staging for the session; nothing recurring)

Until now Kaval had only been tested against accidents. This lab causes a failure on purpose, on
staging, and checks Kaval handles it from the first sign to a fix that holds: a program that needs more
memory than its limit allows. The decisions are in
[ADR-0044](../adr/0044-chaos-with-our-own-jobs-and-a-bounded-memory-fix.md); the run's write-up is
[the post-mortem](../postmortems/2026-10-11-oom.md); what to do about this failure in real life is
[the runbook](../runbooks/pod-oomkilled.md).

## Prerequisites

- [Lab 44](lab-44-recording-whether-a-fix-held.md) (outcomes and `make mttr`) and
  [Lab 45](lab-45-watching-the-watcher.md) (Prometheus on staging) done
- `AWS_PROFILE=kaval`; Docker running (for the policy tests); the staging release pin merged (the
  chaos objects are in the chart, so staging must be running a build that has them)

---

## Step 1 — the new fix, tested without a cluster

The executor learns one new action, `increase_memory_limit`: double a Deployment's memory limit, never
above 512 MiB, refuse when it cannot be done safely.

```bash
pytest services/executor/tests/test_memory_limit.py -q     # 17 tests, fake cluster
docker run --rm -v "$(pwd -W)/policy:/policy" openpolicyagent/opa:1.21.0 test /policy   # 15/15
```

Read the refusals first: at the ceiling, no limit set, two containers with limits, a bare pod. A fix
that knows when not to act is the point.

## Step 2 — the failure maker, tested without a cluster

```bash
pytest services/executor/tests/test_chaos.py scripts/dev/test_chart_chaos.py -q
```

These prove it **refuses** outside local/staging, with the switch off, or when a victim already
exists; that it aborts and cleans up when the switch is turned off mid-run, on timeout and on an
error; and that **the prod render of the chart contains no chaos object at all**.

## Step 3 — prove the fences on the real cluster

`AWS_PROFILE=kaval make staging-up` (about 5 minutes, ~$0.045/hr), then, over Session Manager:

```bash
R=system:serviceaccount:kaval-staging:kaval-staging-chaos-runner
E=system:serviceaccount:kaval-staging:kaval-staging-executor
kubectl auth can-i create deployments -n kaval-demo-staging --as=$R   # yes
kubectl auth can-i create deployments -n kaval-staging       --as=$R   # no
kubectl auth can-i delete pods        -n kaval-demo-staging --as=$R   # no
kubectl auth can-i get secrets        -n kaval-demo-staging --as=$R   # no
kubectl auth can-i patch  deployments -n kaval-demo-staging --as=$E   # yes
kubectl auth can-i create deployments -n kaval-demo-staging --as=$E   # no
kubectl auth can-i delete deployments -n kaval-demo-staging --as=$E   # no
kubectl auth can-i patch  deployments -n kaval-staging       --as=$E   # no
```

Expected: exactly the answers in the comments. **The one that breaks cannot repair, and the one that
repairs cannot break.**

## Step 4 — a game day

Copy [the template](../../chaos/game-day-template.md) to `docs/postmortems/<date>-oom.md` and write the
**before** half: hypothesis, steady state, abort criteria. Then:

```bash
make chaos-run EXPERIMENT=oom      # terminal 1: switch on, start the Job, follow it
# terminal 2, once an incident exists (about a minute):
make chaos-propose                 # staging has no language model: files the diagnosis; prints ACTION <id>
make chaos-approve ACTION=<id>     # the human step. Type y
make chaos-mttr                    # after about 6 minutes: time per stage, from the outcome row
make chaos-status                  # switch false, no Jobs running, no victim
```

What you should see, in order: the victim `OOMKilled` within seconds; an incident within about 80 s;
after approval the executor logs `memory limit 64Mi -> 128Mi`; the runner prints
`"result": "recovered: limit raised and the program stayed up"`; about five minutes after the fix the
outcome row says `resolved`.

`make chaos-stop` is the stop button at any point.

## Step 5 — the same failure with the language model (dev server)

*Done in the follow-up on the dev server; recorded below when run.*

## Step 6 — tear down

`AWS_PROFILE=kaval make staging-down`. Then finish the post-mortem's **after** half the same day.

---

## What went wrong, in the order it happened

1. **A second start ran on top of the first.** The runner correctly refused (`already exists`), but the
   script's exit then switched the shared switch off, which aborted the first run. This is the kill
   switch working, by accident. Fix: refuse before touching the switch.
2. **The helper that files the by-hand diagnosis hung.** It waited for an input stream that the throwaway
   pod never received. Fix: the script travels in an environment variable; the pod's log is read after.
3. **Twelve minutes was not enough for a person.** The run reached the proposal and timed out unapproved.
   Fix: 20 minutes, hard limit 25.
4. **A quoting bug** in the "is a run already going?" check (JSON inside a shell string). Replaced by
   a plain `kubectl get ... | grep`.

Stubbed tests cannot find any of these; only running it can. That is the argument for game days.

## Done when

- [x] `increase_memory_limit` doubles, stops at 512 MiB, refuses at the ceiling, with no limit and when ambiguous; policy class `ask`
- [x] The runner refuses in prod, with the switch off and with a leftover victim; aborts and cleans up on switch-off, timeout and error
- [x] The prod render of the chart contains no chaos object (test and CI step)
- [x] `kubectl auth can-i` gives the answers in Step 3
- [x] Staging: the oom game day ran to `resolved` and `make chaos-mttr` gave real figures (81 s detect, 273 s human, 158 s machine, 431 s wall-clock)
- [x] Post-mortem written the same day
- [ ] The model-written diagnosis of the same failure, on the dev server (Step 5)
