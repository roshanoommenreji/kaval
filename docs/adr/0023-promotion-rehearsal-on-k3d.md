# ADR-0023 — Promotion mechanics rehearsed on k3d, ahead of the real staging cluster

- **Status:** Accepted — built, deployed, verified live
- **Date:** 2026-10-02
- **Deciders:** Roshan

## Context

Phase 3's `ROADMAP.md` carries three related, still-open items: split `deploy/environments/`
into real `staging` and `prod` values files, rehearse the promotion mechanics on k3d, and run
a timed rollback drill. `ADR-0004` already decided what the *real* staging environment is — a
second `t4g.medium` spot node, its own k3s, its own database — and that infrastructure is
explicitly Phase 4 scope (`infra/envs/staging`, `make staging-up`). It doesn't exist yet.

That leaves a question ADR-0004 doesn't answer: is there anything worth proving about
promotion *before* that second cluster is built? The risk of waiting is that "build once,
promote the artifact" and "environments differ only by values" stay untested assertions all
the way through Phase 3, and the first time they're actually exercised is Phase 4, mixed in
with Terraform, a second node, and real spend — a worse place to find a values-file mistake
than a free, disposable local cluster.

## Decision

**Rehearse the promotion mechanics as two Helm releases in one k3d cluster, not as two
clusters.** `kaval-staging` and `kaval-prod` are both deployed into the same `local` k3d
cluster `KAV-46` already built, each as its own Helm release in its own namespace
(`kaval-staging`, `kaval-prod`), using `deploy/environments/staging/values.yaml` and
`deploy/environments/prod/values.yaml` — new, real files, not placeholders.

This is a deliberately narrower claim than ADR-0004's staging environment. It proves the
**mechanics** — a values file, an image reused rather than rebuilt, a `helm rollback` — not
the **isolation** a second node and a second kernel would buy (node-level changes, k3s
upgrades, spot reclamation — ADR-0004's own reasons staging must be a real second cluster).
Namespaces on one node cannot test any of that, which is exactly ADR-0004's argument against
using them as the real staging tier. Here they're fine, because what's being rehearsed is
Helm and the pipeline discipline around it, not infrastructure isolation.

### What the rehearsal actually does

1. **Build once.** Each image (gateway, agent, executor, collector) is built a single time and
   tagged by the commit it was built from (`sha-<short>`), not the floating `:dev` tag every
   other lab has used so far. A floating tag can't prove "the same artifact" — a second
   `docker build` with the same tag can silently produce different bytes.
2. **Deploy to staging first.** `helm upgrade --install kaval-staging ... -f values.yaml -f
   environments/staging/values.yaml --set '*.image.tag=sha-<short>' -n kaval-staging
   --create-namespace`. Verified with the same read-API smoke check `KAV-46`/`47`/`48` already
   used — not a new verification method invented for this story.
3. **Promote the identical tag to prod — never rebuild.** The same `sha-<short>` is passed to
   the `kaval-prod` release. If step 1 only happened once, there is only one possible image
   this command can deploy; "promotion" here is Helm pointing a second release at an artifact
   that already exists, not a second build.
4. **Break prod on purpose, then roll back, and time it.** A deliberately bad value (a
   non-existent image tag, so the rollout fails immediately and visibly) is deployed to
   `kaval-prod`. `helm rollback kaval-prod` restores the previous revision. The wall-clock time
   from running the rollback to the deployment reporting healthy again is recorded — this
   project's first measured time-to-restore, the number `docs/releases/` change records will
   eventually carry for every real release (ADR-0004's "generated change records").

### Why separate demo namespaces per environment

`executor.demoNamespace` (`KAV-47`) defaults to `kaval-demo` for `local`. If `staging` and
`prod` both kept that default while sharing one physical cluster, their executors would be
granted RBAC into the *same* namespace — meaning the deliberate failure injected into prod
for the rollback drill could also be visible to, or interfered with by, whatever staging
happens to be doing at the same time. `staging/values.yaml` and `prod/values.yaml` each set
their own `demoNamespace` (`kaval-demo-staging`, `kaval-demo-prod`). This is itself an
instance of "environments differ only by values" — no template changed to add this isolation,
only the values each environment already carries.

### What stays explicitly out of scope

- **No second node, no Terraform, no `infra/envs/staging`.** That is Phase 4's
  `KAV-32`/ADR-0004 work, unchanged by this ADR.
- **No CI automation** (`release.yml`, `promote.yml`). Every step above is run by hand, the
  same way `KAV-46`–`48`'s live verification was. Scripting the pipeline is Phase 4 scope;
  this story proves the mechanics the pipeline will later automate.
- **No digest pinning by sha256, only by tag.** A real `promote.yml` would pin and verify the
  immutable content digest (`ADR-0004`: "the digest that passed staging"), because a tag can
  be force-moved in a real registry. This rehearsal uses k3d's local image store, which has no
  registry to retag behind anyone's back, so a commit-SHA tag is sufficient to prove the
  principle without building digest-pinning machinery that Phase 4's shared ECR will need for
  real reasons this local cluster doesn't have.

## Consequences

**Easier.** "Build once, promote the artifact" and "environments differ only by values" are
now demonstrated, not just designed. The rollback drill produces this project's first real
measured time-to-restore, ahead of Phase 4 needing one for a change record. A values-file
mistake in `staging/` or `prod/` surfaces now, on a free cluster, instead of during Phase 4's
first real deploy.

**Harder.** None of this is a substitute for Phase 4's real second cluster — a reader could
mistake "promotion mechanics proven" for "staging environment built" if this ADR didn't say
otherwise plainly. The `kaval-staging`/`kaval-prod` namespaces created here are a rehearsal
artifact, not infrastructure that persists; Phase 4 does not reuse them.

**Revisit if:** Phase 4 finds the rehearsal's assumptions didn't hold on real infrastructure
— in particular, whether tag-only promotion (no digest pinning) was in fact fine for k3d but
needs to become digest pinning from day one on the real ECR, rather than an incremental
Phase 4 addition.

## Verification

Run live on the dev server (`kaval-devbox`), inside the same `local` k3d cluster `KAV-46`
provisioned, via `docker --context kaval-devbox` and `kubectl`/`helm` over SSH — the same
toolchain `KAV-47`/`48` used, nothing new installed. Commit `8e29121`.

**Build once.** `gateway`, `agent`, `executor` and `collector` built, then each tagged
`sha-8e29121` — a second, separately-addressable tag on the exact same image, not a rebuild.
`k3d image import` loaded all four into the cluster once.

**Staging first.** `helm upgrade --install kaval-staging ... -n kaval-staging
--create-namespace` with the `sha-8e29121` tags: 5/5 pods `Running`, `/healthz` reported
`postgres.ok: true` (degraded only on the unreachable Ollama, the same expected state every
prior lab recorded — not a new failure).

**Promoted unchanged.** `kaval-prod` deployed with the identical tags, into `kaval-prod`.
6/6 pods `Running` (`gateway.replicas: 2` from `prod/values.yaml` — both ready), same
`/healthz` result. Directly compared the two gateway pods' `imageID`:

```
staging: sha256:6befcf85cc348104457f01128048cba632a1a000a116827658268d50d98864ed
prod:    sha256:6befcf85cc348104457f01128048cba632a1a000a116827658268d50d98864ed
```

Identical — not merely the same tag text, the same content.

**A real finding, not the planned one.** The first break attempt set `gateway.image.tag` to a
non-existent value, expecting a broken rollout to roll back. Instead `helm upgrade` failed
immediately: `pre-upgrade hooks failed: resource Job/kaval-prod/kaval-prod-migrate-2 not
ready`. The migrate Job (`migrate-job.yaml`) runs as a pre-upgrade hook from `gateway.image`,
and it couldn't pull the bad tag either — so Helm refused the upgrade before touching a single
running Deployment. `kaval-prod`'s pods never changed; there was nothing to roll back. Worth
recording plainly: **a bad gateway tag never reaches a running pod, because the migration hook
fails first and blocks the whole release.** That's a stronger guarantee than the rehearsal set
out to test, found by accident rather than by design.

**The real drill.** Broken `executor.image.tag` instead — nothing pre-upgrade depends on it.
`helm upgrade` reported success (hooks don't gate the executor), but the new pod sat in
`ErrImagePull`; Kubernetes' own rolling-update default (`maxUnavailable: 25%` rounds to 0 for a
single-replica Deployment) kept the old, healthy executor pod running the entire time rather
than tearing it down first — so the service was never actually down, only the release was left
in a mixed, half-upgraded state.

`helm rollback kaval-prod 3 -n kaval-prod --wait`, timed start to finish: **1.49 seconds.**
Fast because there was almost nothing to restore — the previous ReplicaSet was already there
and already healthy; rollback's job was to let go of the broken one, not to bring anything back
from nothing. **The honest reading of this drill: for a single-replica Deployment, Kubernetes'
own default rollout strategy is the real safety net, and `helm rollback` cleans up the release
bookkeeping after the fact.** A true all-at-once outage (`Recreate` strategy, or every replica
replaced together) would be a different, slower number — not measured here, and worth it for a
future drill once something in this chart actually uses that strategy.

**Cleanup.** `kaval-staging` and `kaval-prod` released and their namespaces deleted once the
evidence above was captured — a rehearsal artifact, not infrastructure meant to persist, per
this ADR's own Decision section. Node memory returned from 58% to 40%; `kaval-local` (the
Phase 3 environment `KAV-46`–`48` actually run against) was never touched.
