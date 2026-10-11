# ADR-0045 — Rolling back a bad release, and Flux versus direct fixes

- **Status:** Accepted — proven live on staging (see the [post-mortem](../postmortems/README.md))
- **Date:** 2026-10-11
- **Deciders:** Roshan
- **Story:** `KAV-80` (Phase 5, slice 4) · roadmap lines "Chaos CronJobs: crashloop" and "Flux versus direct fixes"

## Context

Slice 3 proved Kaval can fix a program that is killed for lack of memory. The most common real
failure is different: **a new release that crashes the moment it starts**. Two decisions: what the
fix is, and, since Kaval's own services are deployed by Flux from git, whether a fix may be made
directly on the cluster or has to go through git.

## Decision 1 — the fix for a bad release is a rollback, not a restart

A restart brings the pod back running the same broken version, so it crashes again. The fix is to
put the Deployment back to the version it ran before. New executor action **`rollback_deployment`**,
through the unchanged path (policy, human approval, the executor's second policy check, the
five-minute "did it hold?" check).

| What real companies do | Notes |
|---|---|
| Roll back to the previous version (`kubectl rollout undo`, Argo Rollouts, Flux `suspend` + revert) | Fast, and the previous version is known to have run. The default everywhere |
| Roll forward with a fix | Right when the old version cannot run any more (schema change); slower, needs a person to write the fix |
| Automatic canary analysis and rollback (Argo Rollouts, Flagger) | Needs traffic and metrics per version; Kaval has no real traffic |

**Chosen:** rollback, with approval. Properties, all tested without a cluster:

- Target is a Deployment or one of its pods. It takes the pod template of the **previous revision**
  (the older ReplicaSet) and makes it the Deployment's template again, by a JSON patch that
  *replaces* the template, so anything the bad release added (an environment variable, a volume) goes
  too. A merge patch would only overlay the old onto the new.
- **It refuses, rather than guesses:** no earlier version; the earlier version is identical to the
  current one (a restart bumps the revision without changing anything, and a "rollback" would be
  theatre); a bare pod; a Deployment with no selector.
- **A second rollback within an hour is refused.** A rollback makes the old version the newest
  revision, so rolling back again goes *forward* to the broken release just left. The action leaves a
  `kaval.dev/rolled-back-at` mark and refuses while it is under an hour old: a person decides. (The
  same idea as slice 3's "do not keep approving doublings".)
- It cannot tell whether the earlier version is any good. The five-minute outcome check does.
- **Policy class `ask`** at any confidence, blast radius `deployment`, reversible (rolling forward is
  the same operation). The executor's permissions grow with the code: `list` on ReplicaSets (to find
  the earlier version) in the demo namespace, nothing more.

## Decision 2 — Flux versus direct fixes

Kaval's services are deployed by Flux from git (`HelmRelease`, interval 1 minute, reconcile on every
new commit). A fix made directly on the cluster fights that.

| Option | For | Against |
|---|---|---|
| **A. Fix only through git** (the agent proposes a change, a person merges) | Git stays the one truth; the purist GitOps answer | Slow in an emergency; needs a pull-request-writing capability that does not exist, and a write credential to the repository, which is a larger blast radius than a Deployment patch |
| **B. Fix directly, then follow up in git** | Fast; what an on-call engineer does at 3 a.m. | The cluster now differs from git until someone follows up; the next release rewrites the fix |
| **C. Suspend Flux for the object, fix, resume** | Explicit | Easy to forget to resume; a second thing that can go wrong |

**Chosen: direct fixes only where Flux does not own the object; anything Flux owns is a change in
git, by a person.** In practice, today and by construction:

1. **The executor cannot touch what Flux owns.** Its permissions are `Role`s in the demo namespace
   only, never in Kaval's own namespace and never cluster-wide; the chart renders no workload into the
   demo namespace. So nothing Kaval can patch is Flux-managed. Proven, not asserted: the live checks
   in [Lab 47](../labs/lab-47-chaos-bad-release-and-lost-server.md) (`kubectl auth can-i`, and the
   victim carries no Flux labels).
2. **If that ever changes** (a real workload in the demo namespace deployed by Flux), the executor's
   direct patch would stand only until the next release rewrites it. Measured on staging: the
   HelmRelease sets no drift detection, so Flux does **not** keep reverting out-of-band changes (the
   chaos switch was set `"true"` and stayed so for 20 minutes across many reconciles in slice 3);
   it overwrites them at the **next release**, because the chart re-renders what it owns (this is
   deliberately relied on: the switch returns to `"false"` on every deploy). So a direct fix on a
   Flux-owned object is a stopgap with a clock on it, and the proposal must say "then change git".
3. **Not built, on purpose:** an action that writes to git. It needs a repository write credential and
   a review path; deferred until Kaval looks after a workload Flux owns. Recorded as a roadmap line.

## Consequences

- The bad-release game day exercises the full path on a Deployment Flux does not own.
- The runner (the breaker) needs one write it did not have: **`patch` on one named Deployment**
  (`chaos-badrelease`), by `resourceNames`, to ship the "new version". It still cannot patch anything
  else; the executor still cannot create or delete. Tested in the chart.
- **Kaval must tell "killed for memory" from "crashed on its own"**, because the fixes differ (raise
  the limit; roll back). The event feed shows both as `BackOff`, so the collector now reads the pod's
  last exit reason for a `BackOff` and records it; `OOMKilled` there becomes the signal
  `pod_oom_killed`, so the incident cause is `oom_killed`, not `crashloop` (the slice 3 finding). This
  needs `get` on pods for the collector, in the demo namespace.
- **The model cannot propose a rollback yet**, for the same reason it could not propose the memory fix:
  its prompt lists no fixes (`KAV-84`). On staging the diagnosis is filed by hand, as in slice 3.
- Prod: the action ships to prod with the same approval requirement. No chaos runs on prod.
