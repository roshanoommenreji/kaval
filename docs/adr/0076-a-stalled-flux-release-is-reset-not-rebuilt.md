# ADR-0076 — A stalled Flux release is reset, not rebuilt, and `remediateLastFailure` is not adopted

- **Status:** Accepted (plan shown to Roshan 2026-11-03, who said "go"; each live step of the two experiments on staging on 2026-11-04 had its own "go")
- **Date:** 2026-11-04
- **Deciders:** Roshan
- **Story:** `KAV-108` (follows the staging incident of 2026-10-31 and production's of 2026-10-02; evidence in [Lab 76](../labs/lab-76-what-flux-does-when-its-retries-run-out.md); [Lab 67](../labs/lab-67-two-health-pages-and-pods-that-pass-restricted.md) Part 6 and [runbook step 4b](../runbooks/prod-rollout.md) carry the result)

## Context

Production (2026-10-02) and staging (2026-10-31) each got a release that failed on its very first install and then did not recover, even after the cause was fixed in git. Flux showed `MissingRollbackTarget`. Production was cured by deleting the `HelmRelease`; staging was destroyed and rebuilt (about $1 and an hour). All three HelmReleases (staging, production, the EKS lab) retry an install and an upgrade three times (`install.remediation.retries: 3`), and a test guards that.

Flux has a documented setting, `install.remediation.remediateLastFailure`. For an install it defaults to false. With it set to true, Flux removes (uninstalls) the failed release after the last retry. The hypothesis in `KAV-108`: with it on, a failed first install cleans itself up, so the next fix installs on a clean slate and the stuck state cannot happen.

Planning-standard note: this is a settings change to a production release, so it was measured on staging first (the path in [ADR-0004](0004-environment-strategy-and-promotion.md)), on throwaway test releases in their own namespaces, never on Kaval's real release.

## What was measured

Flux v2.9.6 (helm-controller v1.6.5, Helm v4.3.0) on staging, 2026-11-03 to 04. Test chart: the public `podinfo` 6.15.0 (the download was checked against the sha256 in the repository's published index), whose built-in hook job can be made to fail on purpose. Each experiment used two identical releases. **Control** = today's settings (install retries 3). **Candidate** = the same plus `install.remediation.remediateLastFailure: true`.

**Experiment 1: the cause is inside the release.** The pre-install hook job exits 1; the repair is a change to the release's own values (the exit code to 0).

| Step | Control | Candidate |
|---|---|---|
| Failing first install | 4 attempts in about a minute (the first try and three retries), then `Stalled=True/RetriesExceeded` | the same |
| What Flux leaves behind | the failed Helm record, revision 1 `failed` | nothing: `Remediated=True/UninstallSucceeded` |
| After the repair (generation 1 to 2) | `UpgradeSucceeded` in about 17 s: Flux upgraded the leftover failed record (revision 1 `superseded`, revision 2 `deployed`) | `InstallSucceeded` in about 12 s: a fresh install |

**Experiment 2: the cause is outside the release.** A `ResourceQuota` allowing 0 pods in each test namespace stops the job from starting (a stand-in for the stuck migration job of 31 Oct). The repair is deleting the quota by hand; the release is not touched (generation stays 1).

| Step | Control | Candidate |
|---|---|---|
| Failing first install (45 s timeout per try) | 4 attempts, an uninstall between them, then `Stalled=True/RetriesExceeded`; the failed record stays | the same, with no record left |
| Quota deleted, 5 minutes watched (Flux checks every minute) | **no retry by itself** | **no retry by itself** |
| A plain nudge (`flux reconcile helmrelease`) | unchanged: "Failed to install after 4 attempt(s)" | unchanged |
| `flux reconcile helmrelease --reset` | `UpgradeSucceeded` in about 30 s (failed record upgraded) | `InstallSucceeded` in about 30 s (fresh) |

The first prediction, written down before experiment 1, was wrong: the control was expected to stay stuck after the simple repair and to hit `MissingRollbackTarget`. It healed.

## Decision

1. **Do not set `remediateLastFailure: true`** on staging, production or the lab. It only changes what is left behind when Flux gives up (nothing, instead of a failed record). It never changed whether or when Flux tries again, and both states were recovered by the same two actions below. A change to production's release for no measured benefit is not worth making.
2. **The first remedy for a stalled release is `flux reconcile helmrelease <name> -n flux-system --reset`.** It deletes nothing and brought both kinds of leftover back in about 30 seconds. Deleting the `HelmRelease` stays as the fallback if the reset does not work. Rebuilding staging is never needed for this. [Runbook step 4b](../runbooks/prod-rollout.md) is rewritten that way.
3. **The earlier claim is corrected.** [Lab 67](../labs/lab-67-two-health-pages-and-pods-that-pass-restricted.md) and the runbook said a release whose retries ran out "is not tried again by itself, even once the fix is merged". The measured rule is narrower: Flux starts counting again by itself when the release's own configuration changes (shown for a change to its values), and does not when the cause is outside the release (a stuck job, a missing Secret, a quota) until the reset.
4. **No change to the HelmReleases and none to `scripts/dev/test_gitops_remediation.py`**: the three retries and the guard stay as they are.

## Alternatives considered

- **Adopt `remediateLastFailure: true`** (the hypothesis). Rejected: no measured benefit (above), and it makes Flux uninstall whatever a failed install created, in production's release, as a new behaviour.
- **More retries, or `-1` (retry forever).** Not tested. Rejected on design: the retries run out on purpose so that a person looks at a broken release instead of Flux hammering it; and an endless loop would hide a release that is down.
- **Keep deleting the release.** Works, but it is destructive and unnecessary when the reset does the job.

## Not proven

- The exact `MissingRollbackTarget` state of 31 Oct was **not reproduced**. Every upgrade in these tests succeeded (podinfo's pre-install hook does not run on an upgrade, so no upgrade could be made to fail). The reset is shown to work on the stalled states above; that it clears the 31 Oct state is inferred from the same mechanism (it forgets the failure count), not shown. The most likely reading of that day, also unproven, is a second failed attempt, an upgrade of a release that had never been deployed, so there was nothing to roll back to, with the retries then used up again while the stuck job was deleted by hand (which changes nothing Flux watches).
- The trigger on 31 Oct was a new chart **revision** from Git. These tests changed the release's own values. A new chart revision is documented to reset the counters too; it was not tested.
- One run of each experiment, on one node. The test chart's hook job stood in for Kaval's migrate job, and a quota for a stuck job.

## Consequences

- Documentation only; nothing to roll out to staging or production (the whole path: tested on staging, no configuration change to carry to production).
- The runbooks are also the agent's retrieval corpus (`make index-runbooks`), so the corrected step 4b is what it will find.
- **How a stalled release is noticed:** nothing alerts on it today (`KavalLoopStalled` is about Kaval's own loops). The runbook says how to look (`kubectl get helmrelease -A`). Flux notifications by email are the existing, not started story `KAV-53`.
- **A side finding about staging sessions:** staging was left up past 02:00 IST and the nightly auto-stop ([ADR-0008](0008-production-database-on-its-own-server.md)) stopped it. The test releases were lost (a resumed staging gets a new cluster) and the second experiment waited for a resume of about 7 minutes. The staging documentation now says so next to the "four idle hours" rule.
- **Cost:** about $0.20 for the two staging sessions (about 3.7 hours at $0.045 an hour, plus a stopped disk), and the test releases were destroyed with staging.
