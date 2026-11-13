# Lab 98 — The image alarm after a checker restart

**Phase:** 8 (hardening) · **Story:** `KAV-144` · **Decision:** [ADR-0094](../adr/0094-the-cluster-refuses-unsigned-images-on-staging-and-fails-closed.md) (amendment of 2026-11-13) · **Cost:** $0 for Parts 1-3 (files and a promtool run in Docker); about 3-5 cents planned for Part 4 (one staging session of about 45 minutes); Part 5 rides on a production start-up that happens anyway · **Time:** 30 minutes for Parts 1-3

## What you will build

The cluster refuses any Kaval image that is not signed ([Lab 96](lab-96-the-cluster-refuses-unsigned-images.md), [Lab 97](lab-97-production-refuses-unsigned-images.md)), and every refusal should ring the alarm `UnsignedImageSeen`. The live proof on staging found one case where it did not: **a refusal right after the checker restarted**. The refusal itself happened; only the alarm stayed quiet.

This lab finds out why, shows it in a test that fails, fixes the alarm rule, and adds a step to the proof script so the fix can be proved on a real server.

You need the repository, Docker (for promtool, the Prometheus image the chart pins) and Python with the project's dev tools. Parts 1-3 create nothing in AWS. Parts 4 and 5 need a typed go from the owner.

## Part 1 — Why the alarm missed it

Kyverno, the checker, keeps a running count of refusals: `kyverno_image_validating_policy_results_total{result="fail"}`. Prometheus reads it every 30 seconds. The alarm asks "did that count change in the last 15 minutes?" in two ways:

1. **It went up** (`increase(...[15m]) > 0`).
2. **It is new**: it exists now but did not 15 minutes ago (`... unless ... offset 15m`). This catches the very first refusal (a count that appears at 1 has no earlier sample to rise from) and a refusal by a *replaced* checker pod, which has a new address and so a new series.

Now the case staging found. The checker's container restarts **inside the same pod** (same address). Its count starts again at 0. If it refuses an image before Prometheus next reads it, the first reading after the restart is 1. Prometheus saw 1 before the restart and 1 after:

- no increase (1 to 1), and no reset either, because no reading caught the 0;
- not new, because the series has the same name and labels as before.

Both branches are blind. The one number that moved is the checker's own **start time**, `process_start_time_seconds`, which every Go program built with the standard Prometheus library publishes.

**Check that the number really exists before relying on it.** Kyverno 1.20.0's metrics page is served by `promhttp.Handler()` (`pkg/metrics/init.go`), which serves Prometheus's default registry; that registry includes the process collector on Linux (`client_golang`, `registry.go`), and Kyverno's OpenTelemetry exporter registers into the same default registry. Our `kyverno` scrape job keeps every number (only the `cadvisor` job drops numbers), so it is already stored today: the fix adds no new data and no memory. Part 4 checks it on the real server.

## Part 2 — A test that fails first

`deploy/charts/kaval/tests/kaval-image-checker.test.yaml` gained six cases. The one that matters:

```yaml
- name: a refusal right after a checker restart fires
  input_series:
    # an old refusal at 5 minutes; the checker restarts at 60 minutes and refuses again at once
    - series: 'kyverno_image_validating_policy_results_total{policy_name="kaval-images-signed",result="fail",job="kyverno",instance="c:8000"}'
      values: "_x10 1x230"
    - series: 'process_start_time_seconds{job="kyverno",instance="c:8000"}'
      values: "0x120 3600x120"
  alert_rule_test:
    - { eval_time: 50m, alertname: UnsignedImageSeen, exp_alerts: [] }
    - { eval_time: 70m, alertname: UnsignedImageSeen, exp_alerts: [ ...the alert... ] }
    - { eval_time: 80m, alertname: UnsignedImageSeen, exp_alerts: [] }
```

Run only this file against the **old** rule:

```bash
image=$(sed -n 's|^  image: \(quay.io/prometheus/prometheus:[^ ]*\).*|\1|p' deploy/charts/kaval/values.yaml)
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W 2>/dev/null || pwd)/deploy/charts/kaval:/work" \
  -w /work/tests --entrypoint promtool "$image" test rules kaval-image-checker.test.yaml
```

Result on 2026-11-13: `FAILED: a refusal right after a checker restart fires ... exp: [UnsignedImageSeen] got: []`. Every other case passed. That is the blind spot, written down as a test.

The negative cases, which must stay quiet:

| Case | Why it must be quiet |
|---|---|
| A restart with no new refusal | The old count is gone with the old process; nothing new happened |
| A restart whose count comes back at 0 | Same, if Kyverno publishes a zero |
| An old refusal on a checker that has run for hours | Nothing new |
| Another policy's refusal, even right after a restart | Not ours |

And one more that must fire: a refusal from a **replaced** pod at a new address (it already did before the fix; the test keeps it that way).

**A lesson from writing them.** The "restart with no new refusal" case first fired wrongly. The test series simply stopped (`_`), and Prometheus keeps using a last value for up to five minutes. Real Prometheus does not do that here: when a number leaves a scrape it writes a *staleness marker* and the number is gone at once. promtool has the same marker, `stale`; with it the test matches real behaviour and passes. A test that cannot tell "gone" from "not read yet" would have pushed the rule the wrong way.

## Part 3 — The fix

A third branch in `deploy/charts/kaval/files/kaval-image-checker.rules.yaml`:

```
or
sum(kyverno_image_validating_policy_results_total{policy_name="kaval-images-signed",result="fail"}
    and on(instance) (time() - process_start_time_seconds{job="kyverno"} < 900)) > 0
```

In words: **if the checker started less than 15 minutes ago, any refusal it shows is new**, because its count started at 0. `on(instance)` ties the count to the start time of the same checker. It rings for up to 15 minutes, like the other two branches, and then goes quiet.

Options considered (the decision is in the [ADR-0094 amendment](../adr/0094-the-cluster-refuses-unsigned-images-on-staging-and-fails-closed.md)):

- **Ring on every checker restart.** Simple, but it rings on harmless restarts, which teaches people to ignore it.
- **Make the refusal alarm see restarts** (chosen). It rings only when there was a refusal, uses a number we already store, and adds no new alarm to learn.
- **Count refusals from the cluster's event log in a log system.** Survives anything, but we run no log system, and it would cost memory the 4 GB server does not have.

Run all the rule tests:

```bash
make rules-test   # needs Docker
```

Result on 2026-11-13: all six rule files `SUCCESS`.

## Part 4 — The live proof on staging (his typed go)

`scripts/ops/checker_proof.py` has a new step, `restart`. It stops the checker's **container** on the node (`crictl stop`; our nodes install k3s from its binary, which puts no `crictl` on the PATH, so the step uses `k3s crictl` there, found live), so the kubelet starts it again **inside the same pod**. Deleting the pod would prove nothing: a new pod gets a new address, which the alarm always caught. The step prints the pod's address, restart count and `process_start_time_seconds` before and after, and waits until the checker's hook is back (a clean stop removes it for a few seconds; [ADR-0094](../adr/0094-the-cluster-refuses-unsigned-images-on-staging-and-fails-closed.md) point 5).

The order matters. The proof must make branches 1 and 2 blind, so the old refusal has to be more than 15 minutes old when the checker restarts:

```bash
make staging-up                                      # ASSUME_YES=1 when not interactive
python scripts/ops/checker_proof.py staging plant --yes
python scripts/ops/checker_proof.py staging unsigned --yes   # refused; counter fail 1
python scripts/ops/checker_proof.py staging alerts           # UnsignedImageSeen firing
# wait about 16 minutes, until `alerts` shows UnsignedImageSeen inactive
python scripts/ops/checker_proof.py staging restart --yes    # same pod and address, restarts +1, new start time
python scripts/ops/checker_proof.py staging unsigned --yes   # ONLY if restart printed restarts +1; refused at once; counter fail 1 again
python scripts/ops/checker_proof.py staging alerts           # expect: UnsignedImageSeen firing (only the new branch can explain it)
# negative case: wait until it is inactive again, restart once more without a refusal
python scripts/ops/checker_proof.py staging restart --yes
python scripts/ops/checker_proof.py staging alerts           # expect: inactive
python scripts/ops/checker_proof.py staging clean --yes
python scripts/ops/checker_proof.py staging unplant --yes
make staging-down
```

If `restart` prints `no running checker container: nothing restarted`, the checker's container is not named `kyverno` in this chart version, or the node has no container tool: read `k3s crictl ps --label io.kubernetes.pod.namespace=kyverno` on the node and fix the step. It never restarts anything else. **Do not refuse anything after a restart that did not happen** (it happened once: see the results).

**The count after the restart must reach the count before it.** With a count of 1 before, one refusal after is the blind case. If the count is already higher (say 2), one refusal after the restart reads 1, the old rule sees 2 → 1 as a restart and rings anyway, and the proof shows nothing; make as many refusals, seconds apart, as the count read before.

**Results, 2026-11-13 (his `go staging smoke`; staging up 20:32 to about 22:30, about 15 cents):**

| Step | Seen |
|---|---|
| Staging up, 9 apps running; the new rule loaded | `UnsignedImageSeen` had 3 branches and used `process_start_time_seconds`; both alerts inactive |
| First refusal (20:47) | Pod refused ("failed to verify cosign signatures"), `fail` 1; the alarm rang at 20:50 through **branch 2** (a new count) only |
| Alarm quiet again | 21:03 |
| In-place restart, first try | **Failed, safely:** `crictl: command not found` (k3s from its binary has only `k3s crictl`); nothing restarted. I then ran the refusal anyway, by mistake, which made the count 2 and the old rule could see 2 → 1 as a restart, so I waited out the alarm (22:05) and fixed the step |
| In-place restart (22:05:53) | Same pod, same address, `restarts=1`, start time moved; hooks back in 3 s |
| Two refusals seconds apart (22:06) | Pods refused, count read **2 again** (the old value): branch 1 = 0, branch 2 = none (both blind), **branch 3 = 2, the alarm rang** from 22:06 to 22:21 (checker age 900 s) |
| Restart with no refusal (22:22), watched 3 minutes | `restarts=2`, the old count vanished with the old process, all branches empty, **alarm inactive** |
| Clean-up | Test pods gone, planted name removed, real tags untouched, staging deleted |

The headline row is the sixth: with the old rule the alarm stays silent there. Only the new branch rang.

**What the run taught**
- The step assumed `crictl` was on the PATH. It is not on a k3s node installed from the binary: the step now uses `command -v crictl || k3s crictl` (a test pins it).
- The proof needs the count after the restart to reach the count before it. After my stray refusal the count was 2, so two refusals were needed. The lab says so above.
- My script made the refusal even though the restart had failed. The rerun only refused after it had printed `restarts=1`.

## Part 5 — Production (at its next start-up)

The same rule file reaches production through the same chart. At production's next start-up (his usual typed phrases), run `state`, `alerts` and the frozen-list step that [Lab 97](lab-97-production-refuses-unsigned-images.md) could not see (`frozen --yes`, expect: accepted). The restart drill is not repeated on production; the rule is the same file staging proved. If the session lasts more than six hours, `state` also shows the checker's registry login was rewritten by the 6-hourly timer (`ecr-cred` in `kyverno`, its age), and a signed pod is still accepted.

*Results: not run yet (waits for production's next start-up).*

## Things worth knowing

- A count that starts again at 0 is normal for every program that restarts; `increase()` and `rate()` handle it when a reading catches the drop. The blind spot needs the drop to happen **between** two readings and the new count to reach the old value, which is easy when the old value is 1.
- A test that fakes "the number disappeared" must say `stale`, or it tests a Prometheus that does not exist.
- The refusal itself never depended on the alarm: the pod was refused, an Event was written and the Deployment showed the error. The fix is about a person being told.
