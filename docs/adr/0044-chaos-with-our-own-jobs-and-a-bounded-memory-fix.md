# ADR-0044 — Chaos with our own small jobs, and a bounded memory-limit fix

- **Status:** Accepted — proven live on staging ([post-mortem](../postmortems/2026-10-11-oom.md))
- **Date:** 2026-10-11
- **Deciders:** Roshan
- **Story:** `KAV-79` (Phase 5, slice 3) · roadmap lines "Chaos tooling decision, as an ADR" and "Blast-radius containment"

## Context

Phase 5 breaks Kaval's world on purpose and checks that Kaval repairs it, or, for some failures,
that it correctly refuses to and tells a person. Two decisions come first: what causes the
damage, and what the first repair is.

## Decision 1 — what causes the damage

**Our own small chaos jobs.** One suspended Kubernetes CronJob per experiment, started by a person
(`make chaos-run`), running a short Python module (`kaval_executor.chaos`) that creates the failure,
watches the outcome, writes down the timeline and always cleans up.

| Alternative | Why not now |
|---|---|
| **Chaos Mesh / LitmusChaos** (what most teams with many services adopt) | An always-on install: its own controller, webhooks and custom resource definitions, cluster-wide permissions by design, on a 4 GB node where Prometheus alone took 80 MiB. Their fault library (network partitions, clock skew, pod chaos across hundreds of workloads) is for a fleet; Kaval needs five experiments on one namespace. A tool with cluster-wide write permissions is also exactly what the "the agent never gets write access" rule warns against |
| AWS Fault Injection Service | Real and good for the lost-server and disk experiments, and it costs per action-minute. Revisit for slice 4's lost server (KAV-80) |
| Run `kubectl` by hand | No hypothesis, no clock, no clean-up, nothing a stranger can repeat |

**What would make us switch:** more than a handful of experiments needing network or clock faults,
or a second team running its own game days. Then Chaos Mesh in the same `chaos.enabled` slot, with
the same switch and the same namespace fence.

## Decision 2 — containment

1. **The breaker is not the fixer.** The runner has its own ServiceAccount whose only write grant is
   to create and delete Deployments in the demo namespace (a RoleBinding there, never a
   ClusterRoleBinding), plus reading pods and one named ConfigMap. The executor, which repairs,
   cannot create a Deployment; the runner, which breaks, cannot patch one. It has no database
   credential and does not touch policy.
2. **Off in prod, structurally.** `chaos.enabled` is false in the chart; staging and local turn it
   on. A test renders every environment and fails if the prod render contains a chaos object, and CI
   greps the prod render for the word. The runner itself also refuses any environment that is not
   `local` or `staging`.
3. **Three stops:** (a) a switch (a ConfigMap rendered `"false"` on every deploy, turned on by
   `make chaos-run`, turned off at the end or by `make chaos-stop`; the runner aborts and cleans up
   if it goes off mid-run); (b) the CronJob is suspended so nothing runs by itself; (c) the Job has a
   hard 25-minute limit whatever the code is doing, and the experiment stops itself at 20 (first live run: 12 minutes was too short for a person to answer; the run timed out unapproved).
4. **No new image.** The runner is a different command in the executor image: nothing new to build,
   scan, pin or promote.

**A deviation from the plan, recorded:** the plan said a `kaval-chaos` namespace. The image-pull
Secret (`ecr-cred`) is created by the node's bootstrap script in Kaval's own namespace only; a second
namespace would need an infrastructure change to the bootstrap and, in staging, a node rebuild. The
fence that matters is the identity and what it may touch, not the namespace it is named in, so the
runner lives beside Kaval's services with its own ServiceAccount. The victim runs in the existing
demo namespace, which holds nothing but Kaval's practice workloads.

## Decision 3 — the first repair: `increase_memory_limit`

A new executor action, through the unchanged path (policy, human approval, the executor's second
policy check, the five-minute "did it hold?" check).

- It patches the **Deployment** (the target may be the Deployment or one of its pods) so the change
  survives restarts; only the memory limit changes.
- **At most doubles, never above 512 MiB.** Beyond that it is a leak or a sizing decision for a
  person: at the ceiling the action refuses and the execution is recorded as failed with the reason.
- It refuses to guess: no memory limit set, or several containers with one.
- **Policy class `ask`** at any confidence; nothing is born automatic. Blast radius is `deployment`;
  it is reversible (the execution row records the old value).
- The executor's permissions grow in the same change as the code: `get`, `patch` on Deployments and
  `get` on ReplicaSets in the demo namespace; no `create`, no `delete`, nothing outside it.

**Why not just restart?** Restarting an out-of-memory program starts it again with the same limit,
and it dies again. A restart is the wrong repair for this cause, and the experiment exists in part to
prove Kaval does not reach for it.

## Consequences

- **Staging has no language model**, so on staging the diagnosis step is a proposal filed by hand;
  detection, approval, execution and verification are real. The model-written diagnosis of the same
  failure is shown on the dev server (which has the model). Each run says which it was.
- A deploy always leaves the switch off, because the chart re-renders it `"false"`.
- Flux versus direct fixes (a program Flux itself deployed) is **not** decided here: the victim is not
  Flux-managed, so there is nothing for Flux to undo. That is slice 4's ADR (`KAV-80`).
- **Prod:** no chaos runs on prod in Phase 5. Whether a supervised game day is ever run there is left
  open on purpose; the minimum before asking would be three clean staging runs per experiment, a
  rollback rehearsed that week, and a quiet period agreed in advance.
- **First live result:** noticed in 81 s, fixed 32 s after a person's approval, held; and the first run found
  three defects in the tooling and a window too short for a person (now 20 min), all fixed. Kaval labelled the
  incident `crashloop`, not memory: whether an OOM kill is its own cause is for `KAV-80`.
- **The model cannot yet propose this fix:** on the dev server it named the cause but proposed no action in
  3 of 3 runs, because its prompt never lists the available fixes. Not changed here (a prompt change needs
  the golden-incident score before and after): `KAV-84`.
- Thresholds in [service levels](../architecture/service-levels.md) stay provisional until the
  first post-mortem has real numbers: [game-day template](../../chaos/game-day-template.md).
