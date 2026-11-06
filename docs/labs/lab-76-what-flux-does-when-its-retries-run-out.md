# Lab 76 — What Flux does when its retries run out

**Phase:** 7 (EKS chapter, follow-up) · **Story:** `KAV-108` · **Time:** about 90 minutes · **Cost:** about $0.20 (staging at $0.045 an hour for about 3.7 hours; nothing here touches production)

Twice (production 2026-10-02, staging 2026-10-31) a release that failed on its first install did not come back after the cause was fixed, and staging was rebuilt. This lab measures what Flux really does once its retries have run out, with throwaway test releases in their own namespaces, and tests one candidate setting, `install.remediation.remediateLastFailure: true`. The decision it led to is [ADR-0076](../adr/0076-a-stalled-flux-release-is-reset-not-rebuilt.md): the setting is not used, and the first remedy is Flux's `--reset`.

## Prerequisites

- A working AWS login (`AWS_PROFILE=kaval`) and the Session Manager plugin.
- `make staging-up` to build or resume staging (about 6 minutes, $0.045 an hour). **Finish before 02:00 IST:** the nightly auto-stop ([ADR-0008](../adr/0008-production-database-on-its-own-server.md)) stops anything still running then. A stopped staging comes back with a **new cluster**, so any test release on it is gone. (The idle self-stop after four idle hours is a second, separate rule.)
- A shell on the staging node. Either `aws ssm start-session --target <instance-id>` then `sudo -i`, or one script at a time through `aws ssm send-command` with the `AWS-RunShellScript` document. The instance id comes from `aws ec2 describe-instances --filters Name=tag:Name,Values=kaval-staging Name=instance-state-name,Values=running --query "Reservations[].Instances[].InstanceId" --output text`. On the node:

```bash
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml PATH=$PATH:/usr/local/bin
flux version        # this lab: v2.9.6 (helm-controller v1.6.5, Helm v4.3.0)
```

## The test chart

The public `podinfo` chart, version `6.15.0` from `https://stefanprodan.github.io/podinfo`. Check the download before you trust it: its sha256 must equal the `digest` the repository's `index.yaml` publishes for that version (`5ca7896889b539e04cdad4df2093ff1ff7576295e7e3ef90a1e1845e3a334d75`). It ships a hook job that can be told to fail (`hooks.preInstall.job.exitCode`), which is the same kind of failure as Kaval's `migrate` job (a `pre-install` hook that does not finish). Nothing here uses Kaval's own release; every object carries the label `kaval.dev/experiment=kav108` so it can be found.

## Part 1 — Experiment 1: the cause is inside the release

Two identical releases. `kav108-a` has today's settings. `kav108-b` adds `remediateLastFailure: true`. The pre-install hook exits 1 on purpose. On the node, `kubectl apply -f` this:

```yaml
apiVersion: v1
kind: Namespace
metadata: {name: kav108-a, labels: {kaval.dev/experiment: kav108}}
---
apiVersion: v1
kind: Namespace
metadata: {name: kav108-b, labels: {kaval.dev/experiment: kav108}}
---
apiVersion: source.toolkit.fluxcd.io/v1
kind: HelmRepository
metadata: {name: kav108-podinfo, namespace: flux-system, labels: {kaval.dev/experiment: kav108}}
spec: {interval: 30m, url: "https://stefanprodan.github.io/podinfo"}
---
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata: {name: kav108-a, namespace: flux-system, labels: {kaval.dev/experiment: kav108}}
spec:
  interval: 1m
  releaseName: kav108-a
  targetNamespace: kav108-a
  timeout: 2m
  chart: {spec: {chart: podinfo, version: "6.15.0", sourceRef: {kind: HelmRepository, name: kav108-podinfo}}}
  values: {replicaCount: 1, hooks: {preInstall: {job: {enabled: true, exitCode: 1}}}}
  install: {remediation: {retries: 3}}
  upgrade: {remediation: {retries: 3}}
---
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata: {name: kav108-b, namespace: flux-system, labels: {kaval.dev/experiment: kav108}}
spec:
  interval: 1m
  releaseName: kav108-b
  targetNamespace: kav108-b
  timeout: 2m
  chart: {spec: {chart: podinfo, version: "6.15.0", sourceRef: {kind: HelmRepository, name: kav108-podinfo}}}
  values: {replicaCount: 1, hooks: {preInstall: {job: {enabled: true, exitCode: 1}}}}
  install: {remediation: {retries: 3, remediateLastFailure: true}}
  upgrade: {remediation: {retries: 3}}
```

Watch both. **Flux keeps Helm's own records in the `HelmRelease`'s namespace (`flux-system`), not in the target namespace**, so `helm` needs `-n flux-system` (looking in the target namespace says "release: not found" and means nothing):

```bash
for r in kav108-a kav108-b; do
  echo "== $r"
  kubectl get helmrelease $r -n flux-system -o jsonpath='{range .status.conditions[*]}{.type}={.status}/{.reason} {end}{"\n"}'
  helm history $r -n flux-system
done
```

| After about a minute | `kav108-a` (today's settings) | `kav108-b` (the setting on) |
|---|---|---|
| Conditions | `Stalled=True/RetriesExceeded`, `Ready=False/InstallFailed` | the same, plus `Remediated=True/UninstallSucceeded` |
| `helm history` | revision 1, `failed` (the leftover) | `release: not found` (nothing left) |
| Message | `failed pre-install: failed early due to stalled resources: [Job/... status: 'Failed']` | the same |

Repair both the same way, by changing the release's own values (the hook now succeeds), then look again:

```bash
for r in kav108-a kav108-b; do
  kubectl patch helmrelease $r -n flux-system --type merge \
    -p '{"spec":{"values":{"hooks":{"preInstall":{"job":{"exitCode":0}}}}}}'
done
```

| After the repair (generation 1 to 2) | `kav108-a` | `kav108-b` |
|---|---|---|
| Result | `Ready=True/UpgradeSucceeded` in about 17 s: Flux **upgraded the leftover failed record** (revision 1 `superseded`, revision 2 `deployed`) | `Ready=True/InstallSucceeded` in about 12 s: a fresh install (revision 1 `deployed`) |

**What it shows.** My prediction, written down first, was that `kav108-a` would stay stuck after the simple repair, as staging did on 31 Oct. It healed. The setting only changed what was left behind.

## Part 2 — Experiment 2: the cause is outside the release

Now the cause is something Flux cannot see in the release: a `ResourceQuota` that allows no pods in the namespace, so the hook job cannot start (a stand-in for the stuck migration job that was deleted by hand on 31 Oct). The hook itself would succeed (`exitCode: 0`). If staging was stopped or resumed since Part 1, apply the `HelmRepository` from Part 1 again. Then:

```yaml
apiVersion: v1
kind: Namespace
metadata: {name: kav108-c, labels: {kaval.dev/experiment: kav108}}
---
apiVersion: v1
kind: ResourceQuota
metadata: {name: block, namespace: kav108-c, labels: {kaval.dev/experiment: kav108}}
spec: {hard: {pods: "0"}}
---
apiVersion: v1
kind: Namespace
metadata: {name: kav108-d, labels: {kaval.dev/experiment: kav108}}
---
apiVersion: v1
kind: ResourceQuota
metadata: {name: block, namespace: kav108-d, labels: {kaval.dev/experiment: kav108}}
spec: {hard: {pods: "0"}}
---
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata: {name: kav108-c, namespace: flux-system, labels: {kaval.dev/experiment: kav108}}
spec:
  interval: 1m
  releaseName: kav108-c
  targetNamespace: kav108-c
  timeout: 45s
  chart: {spec: {chart: podinfo, version: "6.15.0", sourceRef: {kind: HelmRepository, name: kav108-podinfo}}}
  values: {replicaCount: 1, hooks: {preInstall: {job: {enabled: true, exitCode: 0}}}}
  install: {remediation: {retries: 3}}
  upgrade: {remediation: {retries: 3}}
---
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata: {name: kav108-d, namespace: flux-system, labels: {kaval.dev/experiment: kav108}}
spec:
  interval: 1m
  releaseName: kav108-d
  targetNamespace: kav108-d
  timeout: 45s
  chart: {spec: {chart: podinfo, version: "6.15.0", sourceRef: {kind: HelmRepository, name: kav108-podinfo}}}
  values: {replicaCount: 1, hooks: {preInstall: {job: {enabled: true, exitCode: 0}}}}
  install: {remediation: {retries: 3, remediateLastFailure: true}}
  upgrade: {remediation: {retries: 3}}
```

Each attempt takes the 45-second timeout (`timeout waiting for: [Job/... status: 'InProgress']`), and Flux uninstalls between attempts. After four attempts (about three and a half minutes) both are `Stalled=True/RetriesExceeded`; `kav108-c` keeps a failed record, `kav108-d` has none. (One attempt of `kav108-d` failed with "client rate limiter Wait returned an error ... would exceed context deadline" instead: two releases at once with a short timeout; the outcome was the same.)

Fix the cause by hand and touch nothing else, then watch for five minutes:

```bash
kubectl delete resourcequota block -n kav108-c
kubectl delete resourcequota block -n kav108-d
kubectl get helmrelease kav108-c kav108-d -n flux-system -o jsonpath='{range .items[*]}{.metadata.name} generation={.metadata.generation}{"\n"}{end}'   # still 1
```

Prediction, written first: neither retries by itself. Result: **no change in five minutes** for either (Flux checks every minute, so it looked five times). Now the two remedies, in order:

```bash
# 1. a plain nudge: asks Flux to look again now, does not reset the failure count
flux reconcile helmrelease kav108-c -n flux-system --timeout=40s    # "Failed to install after 4 attempt(s)"
flux reconcile helmrelease kav108-d -n flux-system --timeout=40s    # the same
# 2. the reset: forgets the earlier failures and tries again from zero
flux reconcile helmrelease kav108-c -n flux-system --reset --timeout=90s
flux reconcile helmrelease kav108-d -n flux-system --reset --timeout=90s
```

| | `kav108-c` (today's settings) | `kav108-d` (the setting on) |
|---|---|---|
| After the nudge | unchanged, still stalled | unchanged, still stalled |
| After the reset (about 30 s) | `Ready=True/UpgradeSucceeded` (failed record upgraded; revision 1 `superseded`, 2 `deployed`) | `Ready=True/InstallSucceeded` (fresh; revision 1 `deployed`) |
| `status.lastHandledResetAt` | set to the time of the reset | set to the time of the reset |

## Part 3 — Clean up

`ASSUME_YES=1 make staging-down` destroys the cluster, so the test releases need no separate removal (about 4 minutes, 92 resources). Then check nothing is left: its closing line prints the instances, volumes and VPCs tagged `Env=staging`, and a sweep by name and by tag found only snapshots (see "What went wrong"). Do not rely on the tag search alone: it lags and still listed volumes, snapshots and servers that were already gone. Look at each id directly.

## What went wrong along the way (kept on purpose)

- **My prediction for experiment 1 was wrong.** The control healed. Only experiment 2 reproduced "stuck until reset".
- **I first looked for Helm's records in the wrong namespace** and read "release: not found" as "removed". Flux keeps them in `flux-system`. Nothing was concluded from that column before it was re-read.
- **Staging was left up past 02:00 IST** and the nightly auto-stop stopped it, which also removed the test releases. The second experiment needed a resume (about 7 minutes) and the chart shortcut applied again. My own notes had only the idle rule.
- **The nightly stop's snapshots are not cleaned by `make staging-down`** (it deletes by a name pattern they do not carry). Three were found afterwards: `KAV-127` (fixed on 2026-11-06 by finding the copies by their labels; [Lab 78](lab-78-backup-copies-that-belong-to-the-right-environment.md)).
- **Reading the logs showed two real defects** unrelated to Flux: the four service-login passwords printed into the node's logs (`KAV-125`), and a fresh staging reporting "Seed status: skipped" after a good seed (`KAV-126`).

## Done when

- [x] Experiment 1 measured: with and without `remediateLastFailure`, a failed first install and a repair that changes the release's own values *(2026-11-04)*
- [x] Experiment 2 measured: a cause outside the release, fixed by hand; no retry by itself; a plain nudge does nothing; `--reset` repairs both *(2026-11-04)*
- [x] The decision is recorded: [ADR-0076](../adr/0076-a-stalled-flux-release-is-reset-not-rebuilt.md); the setting is not adopted, nothing changes in the HelmReleases
- [x] [Runbook step 4b](../runbooks/prod-rollout.md) starts with the reset; [Lab 67](lab-67-two-health-pages-and-pods-that-pass-restricted.md)'s claim is corrected
- [x] Merged with CI green *(2026-11-04: merged as #312 with all 11 checks green; `KAV-108` Done in Jira on 2026-11-05)*

## Cost

About $0.20: staging from 23:26 on 3 Nov to the nightly stop at 02:00 (about 2.6 hours) and from 21:20 to 22:25 on 4 Nov (about 1.1 hours), $0.045 an hour. The stopped database disk and three leftover 10 GB snapshots cost a few cents until they were deleted (the snapshots on 2026-11-05, with Roshan's go). Everything else was free. Nothing here touched production.
