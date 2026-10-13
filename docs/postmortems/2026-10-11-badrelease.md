# Game day: badrelease — 2026-10-11

Second game day. Blameless: this describes what the system did. The person running it is also the
person on call. Template: [chaos/game-day-template.md](../../chaos/game-day-template.md). Design:
[ADR-0045](../adr/0045-rolling-back-a-bad-release-and-flux-versus-direct-fixes.md).

## Before (written before the run started)

| | |
|---|---|
| **Experiment** | `badrelease`: a healthy program (`v1`) is started in `kaval-demo-staging`, then changed to a `v2` that prints a configuration error and exits at once, every start |
| **Environment** | staging only (build `sha-2ac5de6`) |
| **Who is watching** | Roshan (approves the fix) and Claude (runs and records). If it goes wrong, `make chaos-stop` |
| **Hypothesis** | Kaval opens an incident for the victim within 5 minutes of the first crash, and labels it `crashloop` (not out-of-memory, because the program exits with an error, not `OOMKilled`). After the by-hand proposal is filed and approved, the executor puts the Deployment back to `v1` (not a restart: a restart would return the same `v2`), the program stays up for 5 minutes and the outcome row says resolved |
| **Steady state** | Checked 17:48 UTC: all six pods Running with 0 restarts, no incidents at all (gateway `/incidents` empty), demo namespace empty, node memory 1423 of 3829 MiB (37 %), chaos switch `false` |
| **What the run may touch** | Only the Deployment `chaos-badrelease` in `kaval-demo-staging`. Proved before the run with `kubectl auth can-i`: the runner may patch `chaos-badrelease` and not `chaos-oomer`, nor anything in Kaval's namespace; the executor may create no Deployment and patch none in Kaval's namespace, and may list ReplicaSets in the demo namespace; the collector may get a pod, not list or delete one |
| **Abort if** | An incident appears for a Kaval service; node memory passes 85 %; any pod outside the demo namespace restarts; the run passes 15 minutes. Abort = `make chaos-stop` |
| **Kill switch** | `make chaos-stop` |
| **Cost** | staging up for the session, about $0.045/hr |
| **Model** | none on staging: the diagnosis is a proposal filed by hand (`make chaos-propose EXPERIMENT=badrelease`), with the policy class still decided by the real policy engine. Detection, approval, execution and the five-minute check are real |
| **Also checked** | the victim is not owned by Flux (no Flux labels); node memory is sampled **during** the run (slice 3 missed the peak) |

## During (UTC, from the cluster's own clock)

| Time | What happened | Where it was seen |
|---|---|---|
| 17:49:54 | Runner creates `chaos-badrelease` at `v1` (a program that prints and sleeps) | runner timeline, second 0 |
| +10 s | `v1` Ready; the runner ships `v2` (prints a configuration error, exits 1 at once) | runner timeline |
| +20 s | First crash of `v2` (`Error`, then `BackOff`) | runner timeline, pod status |
| 17:50:19 | Incident `crashloop:k8s:kaval-demo-staging/chaos-badrelease` opened (medium) | gateway `/incidents` |
| 17:51:46 | Stand-in diagnosis filed (`make chaos-propose EXPERIMENT=badrelease`): action `rollback_deployment`, policy class **ask** from the real engine | proposal row |
| 17:52:50 | Approved by a person (`make chaos-approve`), HTTP 201 | decision row |
| about 17:53:10 | Executor picks it up and acts: `rolled back from revision 2 to revision 1`; execution `success` | executor log, execution row |
| 17:53:15 | Runner sees the Deployment back at `v1` | runner timeline, second 200 |
| 17:54:15 | Program has stayed up 60 s; runner deletes the victim and switches off | runner timeline, second 260 |
| about 17:58 | Executor's own check, five minutes after the fix: resolved | outcome row (`make chaos-mttr`) |

## After

| Question | Answer |
|---|---|
| Was the hypothesis true? | **Yes, every part.** Incident well inside 5 minutes, labelled `crashloop` (not out-of-memory); the Deployment was put back to `v1`, not restarted; it stayed up; the outcome row says resolved |
| Detect · diagnose · human · queue · execute | **66 s · 12 s · 74 s · 4 s · 12 s** (`make chaos-mttr`) |
| Machine time / wall-clock | **94 s** / **168 s**. A person's answer was 44 % of the wall-clock time |
| Did the fix hold five minutes? | Yes (outcome row present and resolved) |
| Alerts that should be silent | Silent. The only incident in staging was the experiment's own; no Kaval pod restarted (all `0` restarts) |
| Node memory | 1423 MiB (37 %) before; **1501 MiB (39 %) sampled during the run** (the crash loop, about a minute in); 1491 MiB (38 %) after. The crash loop costs a few tens of MiB at most |
| Was it Flux's? | **No.** The victim carries no Flux labels (its labels are `app` and `kaval.dev/chaos` only), so nothing could undo the fix. See ADR-0045 |
| Cleaned up? | Yes: switch `false`, no Job running, no victim, demo namespace empty |

The diagnose figure (12 s) is again the stand-in's, not a measurement of Kaval.

### What we learned, and what changes

1. **The labelling fix worked.** This incident is `crashloop`, because the container ended with `Error`.
   The out-of-memory kill in slice 3 was labelled `crashloop` too; with the collector now reading the
   pod's last exit reason, that one becomes `oom_killed`. The collector change is covered by unit tests
   and this run (the crash side); the out-of-memory side is re-proved on the next oom run.
2. **A bad release with no readiness check takes the whole service down, not just a stuck update.**
   The new pod counted as Ready for the second before it crashed, so Kubernetes removed the good
   version straight away (the `v1` ReplicaSet scaled to 0 within a second of `v2` appearing; the
   Deployment reported "does not have minimum availability"). A readiness probe on the program would
   have kept `v1` serving. **Kaval's own loop workers (agent, collector, executor) have no readiness
   probe either** (only the gateway, Prometheus and the database chart have one), so a bad release
   of one of them would take the old version down the same way. Noted for the close-out (`KAV-83`);
   the advice for any service is in the runbook
   ([`pod-crashloop-after-release.md`](../runbooks/pod-crashloop-after-release.md)).
3. **A rollback is a real fix, a restart would not have been.** The pod name changed hash
   (`66d9b58c47` to `8667898`): it is the old ReplicaSet being brought back, which is exactly what the
   action does. The unit tests also pin the cases the live run did not meet (no earlier version,
   identical versions, a second rollback within the hour).
4. **Human time was shorter than last time (74 s against 273 s).** One sample each, so no conclusion
   about why. It is still the largest single piece after detection. Approval expiry and escalation are `KAV-82`.
5. **The tooling needed no repair on the day.** The three lessons of slice 3 (refuse before touching the
   switch, a 20-minute window, scripts passed by environment) held.
6. **The Flux question was answered by construction, not by a live probe.** I intended to scale a
   Flux-owned Deployment down by hand and watch whether Flux restored it; that change to the shared
   cluster was blocked, so it was not run, and I did not work around the block. The ADR rests on
   what was measured in slice 3 instead (the chaos switch, set `"true"` by hand, stayed so for 20
   minutes across many reconciles, so Flux does not keep reverting by-hand changes; every deploy
   resets it, because the chart re-renders what it owns) plus the permissions proof: the executor
   cannot touch Kaval's own namespace at all.

### Service levels

Second real sample against [service levels](../architecture/service-levels.md): detect 66 s (target 95 %
within 120 s: met, two samples of two); machine time to restore 94 s (target median 5 minutes: met);
two fixes held out of two; no unsafe action. Two runs are still not a rate: the targets stay provisional.

## Follow-up, 2026-10-13: the finding is closed

Item 2 above (Kaval's own background programs had no readiness check, so a bad release of one of them
would take the working copy down too) was fixed in `KAV-83` ([ADR-0051](../adr/0051-closing-phase-5-readiness-checks-prod-prometheus-and-the-disk-trend.md)).
Proof on staging, the same agent released twice as a program that starts, runs 20 seconds and dies: with the
check the old pod stayed `1/1 Running` for the whole 80 seconds and the new one stayed `0/1`; without it
the old pod was removed within 10 to 40 seconds and nothing was left serving. Details and timeline in
[Lab 50](../labs/lab-50-closing-phase-5.md).
