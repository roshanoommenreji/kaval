# Game day: oom, diagnosed by Kaval itself, and the model switched off — 2026-10-15

Blameless: this describes what the system did. The person running it is also the person on call. Template:
[chaos/game-day-template.md](../../chaos/game-day-template.md). Design:
[ADR-0055](../adr/0055-the-model-runs-in-the-cluster-and-diagnoses-new-incidents.md) (`KAV-87`). First game day where
the proposal was **not** filed by hand.

## Before (written before the runs started)

| | |
|---|---|
| **Experiment** | `oom` (same failure as 2026-10-11), then `disk`, then `oom` again with the model switched off, then `oom` a last time |
| **Environment** | staging only (build `sha-b36c4be`; PR #170's grant applied part-way, see below) |
| **Who is watching** | Roshan (approves the fix) and Claude (runs and records). If it goes wrong, `make chaos-stop` |
| **Hypothesis 1** | A new out-of-memory incident gets **one** proposal from the diagnosis worker with nobody running a command, and none on the passes after. A full-disk incident (for a person) gets none |
| **Hypothesis 2** | With the model scaled to zero the incident stays open and undiagnosed, `KavalDiagnosisBacklog` fires about 18 minutes in, the correlator and the executor keep working; with the model back the one proposal appears and the alarm clears |
| **Hypothesis 3** | An approved worker-filed proposal raises the memory limit and the program stays up |
| **Steady state** | Checked 15:58 UTC: all eight pods Running on `sha-b36c4be`, node 1,955 MiB (51 %), model pod 138 MiB, nothing waiting |
| **What the run may touch** | Only `chaos-oomer` / `chaos-diskfill` in `kaval-demo-staging`, plus scaling the model Deployment to 0 and back |
| **Abort if** | An incident appears for a Kaval service other than the diagnoser's own alarm; node memory passes 85 %; a pod outside the demo namespace restarts; a run passes 25 minutes |
| **Kill switch** | `make chaos-stop`; the model is brought back with `kubectl scale ... --replicas=1` |
| **Cost** | staging about $0.045/hr, up about 2 h 45 min |
| **Model** | the real one, in the cluster (`gemma3:1b-it-qat`) |

## During (UTC, cluster clock)

**Run 1, out-of-memory (15:59)**

| Time | What happened |
|---|---|
| 15:59:16 | Failure started; first `OOMKilled` 10 s later |
| about 16:00 | Incident `oom_killed:...chaos-oomer` opened |
| 16:01:17 to 16:01:38 | Node memory 1,707 to 2,795 MB used: the model loaded (+1.1 GB) |
| 16:01:50 | Model call returned: 36.4 s (1,625 tokens read at 68 per second, 102 written at 15 per second) |
| 16:01:51 | Worker: `ok ... (0 still waiting) proposal 7473de17..., 1 action(s)`; every pass after: `nothing waiting` |
| 16:06:50 | Model unloaded (5 minutes idle); node back to 1,540 MB used |
| 16:19 (my approval) | **Refused: expired.** Staging gives an approval 15 minutes; I took 17 |
| 16:19 to 16:20 | I edited `chaos.sh` while the run was still using it; the running copy hit a syntax error, cleaned up early and removed the victim. The approved by-hand fix then failed safely: `NotFoundException` |

**Run 2, full disk (16:22 to 16:34)**: the failure was held 720 s; `make chaos-check` passed against the node's
`disk_pressure` incident (already open since 15:55 from the node's own disk at 66 %): a person is told, nothing proposed or
run. The worker log shows no `ok` for it. During this run the worker, handed an incident with history, was refused reading `outcome` and restarted
(8 restarts in all) before idling: the bug of PR #170, fixed and applied at 17:48.

**Run 3, out-of-memory with the model off (17:50)**

| Time | What happened |
|---|---|
| 17:50:29 | Model Deployment scaled to 0 |
| 17:51:29 | Failure started; incident opened 17:51:33 |
| 17:52 onward | Worker: `model_error` (Connection refused), then `backing_off` between tries (30 s doubling to 10 min); 6 `model_error` counted |
| 18:07:25 | Oldest wait passes 900 s: `KavalDiagnosisBacklog` pending |
| about 18:09:25 | Fires (2-minute hold); 18:09:51 the poller opens `kaval_diagnosis_backlog:...diagnoser` |
| 18:11:30 | Failure program removed by the runner's own 20-minute limit |
| throughout | Correlator "attached N signals to M open incident(s)"; executor "nothing to execute": both alive |
| 18:13:56 | Model scaled back to 1; Ready at 18:14:31 (35 s; both files re-downloaded and checked) |
| 18:19:09 | Worker's next try after its pause: `ok ... proposal f9a31eb9..., 1 action(s)`; `kaval_diagnosis_waiting` 0, alarm gone |

**Run 4, out-of-memory, clean (18:29)**

| Time | What happened |
|---|---|
| 18:29:11 | Failure started |
| 18:32:05 | `make chaos-propose` found the **worker's** proposal (policy class `ask`) |
| 18:32:11 | Approved (6 s later; a second background job did it so I could not be slow again) |
| 18:32:31 | Limit raised (200 s after the failure began); program stayed up |
| 18:37:31 | Executor's five-minute check recorded the outcome |

## After

| Question | Answer |
|---|---|
| Hypothesis 1 true? | **Yes.** One proposal, nobody typing a command, none repeated; the disk incident got none |
| Hypothesis 2 true? | **Yes.** Open and undiagnosed, alarm about 18 minutes in, nothing else stopped, one proposal after the model returned, alarm cleared |
| Hypothesis 3 true? | **Yes** (run 4) |
| Detect · diagnose · human · queue · execute | **60 s · 25 s · 91 s · 18 s · 0 s** (`make chaos-mttr`) |
| Machine time / wall-clock | **103 s** / **195 s**. First time the diagnose figure is Kaval's own model rather than a stand-in. Earlier oom run: 158 s / 431 s |
| Did anything happen that the hypothesis did not predict? | Yes: three defects, below |
| Alerts that should be silent | Silent. The only extra incident was the diagnoser's own alarm, during the outage, on purpose |
| Node memory | idle 1,640 to 1,725 MB used (about 2,000 MB available); model loaded about 2,840 MB used, **780 MB available at worst**; a few minutes after, 1,540 MB |
| Processor credits | balance 0 to 29.5, surplus charged $0 |
| Cleaned up? | Switch `false`, victim gone, model Deployment back to 1 replica |

### What we learned, and what changes

1. **The worker's database account could not read `outcome`.** The first diagnosis worked only because there was no
   history; the next incident with history crashed it. Nothing else was affected, and the restart count showed it.
   Laptop tests use an administrator's account, so no laptop test could. Fixed in PR #170 (one read-only grant, and a
   test that names every table the worker reads and proves it still cannot write outcomes). The runbook now lists this
   cause. **Tracked:** done.
2. **`CHAOS_BY_HAND=1` never reached the pod that files the proposal.** Fixed and tested in PR #170.
3. **A worker-filed proposal expires like any other, and is not re-made.** Staging's 15 minutes passed while I was
   investigating. By design (ADR-0055, ADR-0048); the runbook says what a person does (`make diagnose`). Watch whether
   it happens in real use before adding an automatic second try.
4. **Never edit a script that is running.** Bash reads a script as it goes. The failure was safe (the fix failed with
   "not found", nothing else was touched) but it cost a re-run. Lab 52 says so.
5. **The alarm fired about 18 minutes after the model went off**, not 15: 15 minutes of waiting plus the 2-minute hold
   plus the poller's pass. The runbook and the ADR already say "over 15 minutes"; nothing to change.
6. **Staging has no Slack**, so "a person is told" is the message prepared by the real alert code (a dry run), as in
   earlier game days. The real Slack message is seen once in prod.

### Service levels

Machine time to restore (#3) was 103 s against a median target of 5 minutes: met. Wall-clock (#6) has no target. The
diagnose stage (#2) is a measurement of Kaval's own model for the first time: 25 s against 90 s on this run. The first
run's incident-to-proposal time was not recorded to the second and likely sat near 90 s because the model was cold
(a 36 s call plus up to 30 s for the worker's next pass), so it counts as an unmeasured near-miss, not a pass.
