# Lab 47 — Chaos: a bad release, a lost server, and telling the two kinds of crash apart

**Phase:** 5, slice 4 · **Time:** ~3 h (build ~1.5 h, three staging game days ~1 h) · **Cost:** about $0.15 (staging for the session; nothing recurring)

Slice 3 broke a program's memory and checked Kaval fixed it. This lab does the two other everyday
failures: **a new release that crashes at start-up**, fixed by going back to the previous version, and
**the server itself disappearing**, which has to heal without anyone acting. It also fixes something the
first game day found: Kaval called a real out-of-memory kill a "crashloop". The decisions are in
[ADR-0045](../adr/0045-rolling-back-a-bad-release-and-flux-versus-direct-fixes.md) and
[ADR-0046](../adr/0046-losing-the-app-server-and-how-we-test-it.md); the write-ups are
[the bad release](../postmortems/2026-10-11-badrelease.md) and
[the lost server](../postmortems/2026-10-12-node.md); the runbooks are
[`pod-crashloop-after-release.md`](../runbooks/pod-crashloop-after-release.md) and
[`node-lost.md`](../runbooks/node-lost.md).

## Prerequisites

- [Lab 46](lab-46-chaos-and-the-memory-game-day.md) done (the runner, the switch, `make chaos-*`)
- `AWS_PROFILE=kaval`; Docker running (for the policy tests); the release pin merged so staging runs a
  build that has the new code (check `make staging-status`)

---

## Step 1 — the rollback fix, tested without a cluster

The executor learns `rollback_deployment`: put a Deployment back to the version it ran before.

```bash
pytest services/executor/tests/test_rollback_deployment.py -q       # 14 tests, fake cluster
docker run --rm -v "$(pwd -W)/policy:/policy" openpolicyagent/opa:1.21.0 test /policy   # 17/17
```

Read the refusals first: no earlier version, an earlier version identical to the current one, a bare
pod, and **a second rollback within an hour** (it would go forward to the broken release). A fix that
knows when not to act is the point.

## Step 2 — telling "killed for memory" from "crashed on its own"

Kubernetes never emits an `OOMKilled` event; it is only in the pod's status. The event feed shows both
failures as `BackOff`, so the first game day's out-of-memory kill was labelled `crashloop`, and the two
need different fixes. The collector now reads the pod's last exit reason for a `BackOff` event.

```bash
pytest services/collector/tests/test_k8s_events.py -q               # needs the test Postgres (Lab 46 notes)
```

The collector's permission grows by exactly one thing, `get` on pods in the demo namespace (and the
chart test checks it cannot list, patch or delete them).

## Step 3 — the breaker and the fences

```bash
pytest services/executor/tests/test_chaos_badrelease.py scripts/dev/test_chart_chaos.py -q
```

The runner may now `patch` **one named Deployment** (`chaos-badrelease`, by `resourceNames`), to ship the
bad "new version". The chart test checks that is the only patch it has, and that the prod render still
holds no chaos object at all.

On staging, prove the fences with `kubectl auth can-i` (over Session Manager):

```bash
R=system:serviceaccount:kaval-staging:kaval-staging-chaos-runner
E=system:serviceaccount:kaval-staging:kaval-staging-executor
C=system:serviceaccount:kaval-staging:kaval-staging-collector
kubectl auth can-i patch deployments/chaos-badrelease -n kaval-demo-staging --as=$R   # yes
kubectl auth can-i patch deployments/chaos-oomer      -n kaval-demo-staging --as=$R   # no
kubectl auth can-i patch deployments                  -n kaval-staging       --as=$R   # no
kubectl auth can-i create deployments -n kaval-demo-staging --as=$E                   # no
kubectl auth can-i list replicasets   -n kaval-demo-staging --as=$E                   # yes
kubectl auth can-i patch deployments  -n kaval-staging       --as=$E                  # no
kubectl auth can-i get pods    -n kaval-demo-staging --as=$C                          # yes
kubectl auth can-i list pods   -n kaval-demo-staging --as=$C                          # no
kubectl auth can-i delete pods -n kaval-demo-staging --as=$C                          # no
```

## Step 4 — game day: a bad release

Copy [the template](../../chaos/game-day-template.md) to `docs/postmortems/<date>-badrelease.md` and write
the **before** half. Then:

```bash
make chaos-run EXPERIMENT=badrelease     # terminal 1: switch on, start v1, ship the bad v2, follow
make chaos-propose EXPERIMENT=badrelease # terminal 2, about a minute in: files the rollback; prints ACTION <id>
make chaos-approve ACTION=<id>           # the human step. Type y
make chaos-mttr                          # after about 6 minutes
```

You should see: the pod `Error`, then `CrashLoopBackOff`; an incident `crashloop:...` within about a
minute; after approval the executor logs `rolled back from revision 2 to revision 1`; the runner prints
`recovered: rolled back and the program stayed up`; the outcome row says resolved.

## Step 5 — game day: lose the app server

This one runs from the laptop, because the cluster cannot test its own death:

```bash
make staging-up        # if not running
make chaos-node        # type the machine id when asked; then wait. It gives up after 30 minutes
```

It prints a JSON block: seconds from ending the machine to the old one gone, a new one running,
the cluster node Ready, every pod ready, the gateway answering with its database check ok, and the
incident written before the loss still there. It refuses (and ends nothing) when the machine is not in
the group `kaval-staging`, when more than one runs, when Kaval is not healthy to begin with, and unless
you type the id.

## Step 6 — the same memory failure, now labelled correctly

```bash
make chaos-run EXPERIMENT=oom ; make chaos-propose ; make chaos-approve ACTION=<id>
```

The incident is now `oom_killed:...` (the slice 3 run said `crashloop:...`) and the signals carry
`last_exit_reason: OOMKilled`, exit code 137.

## Step 7 — tear down

`AWS_PROFILE=kaval make staging-down`. Then finish each post-mortem's **after** half the same day.

---

## What went wrong, in the order it happened

1. **A test file named like an existing one.** `test_rollback.py` already exists under `scripts/release/`
   and pytest refuses two with the same name; the new one is `test_rollback_deployment.py`.
2. **The pre-commit hook caught a 12-digit number in a test** (a fake id of fives). It could not tell it
   from an account id. Changed the fake, did not bypass the hook.
3. **The node-loss script refused twice before ending anything, both times for its own mistakes.** It
   expected an `Env` tag the auto-scaling group never gives its machines; then its "healthy to begin with"
   check used `/healthz`, which is 503 on staging for ever (no language model there). The guard failed
   shut, which is the right way. Fixed: the group name is the identity, healthy means the gateway answers
   and its database check is ok.
4. **The script's step times were stamped before the checks that saw them.** Checked against the
   cluster's own clocks (within about 20 s), then changed to stamp after.
5. **A scale-down of a Flux-owned Deployment, meant to prove whether Flux undoes by-hand changes, was
   blocked** by the session's guard rails and not retried. The ADR rests on slice 3's observation and
   the permissions proof instead.

Stubbed tests cannot find items 3 to 4; only running it can. That is the argument for game days.

## Done when

- [x] `rollback_deployment` goes back one version, refuses with no earlier version, identical versions, a bare pod and a second rollback inside an hour; policy class `ask`
- [x] The runner can patch only its own named victim; the prod render holds no chaos object (test and CI step)
- [x] The collector reads a pod's last exit reason: an OOM kill is `oom_killed`, a crash on its own stays `crashloop` (unit tests and live, both)
- [x] Staging: the bad release was noticed, rolled back on approval and held (`make chaos-mttr`: 66 s detect, 94 s machine, 168 s wall-clock)
- [x] Staging: the lost server was replaced by itself and every pod and the earlier incident came back in about 2 minutes 10 seconds
- [x] Two post-mortems written the same day, two runbooks, two ADRs
