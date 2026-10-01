# ADR-0020 — The Helm chart, and the local k3d environment

- **Status:** Accepted — built, deployed, verified live
- **Date:** 2026-10-01
- **Deciders:** Roshan

## Context

Phase 2 proved the agent loop on bare Postgres via Docker Compose. Phase 3's exit gate —
"kill a pod locally → agent proposes → you approve → executor fixes it" — needs that same loop
running in a real Kubernetes cluster, deployed by the one umbrella Helm chart CLAUDE.md already
commits to (`deploy/charts/kaval`), with environments differing only by values files (ADR-0004).
None of it existed yet: `deploy/charts/kaval/templates/` held only a `.gitkeep`, and
`services/executor/` is still a placeholder.

This story is the foundation the rest of Phase 3 builds on — a real cluster, a real chart,
deploying the part of the stack that already exists (Postgres, gateway, the correlate loop) —
not the whole phase. Real K8s-events-as-signals, the executor with scoped RBAC, the
staging/prod values split, and the timed rollback drill are explicitly separate, later stories.

## Decision

### k3d on the dev server, not a new environment

`k3d` creates a k3s cluster as Docker containers, so it runs on the existing dev server
(ADR-0007) using the Docker daemon already there — no new infrastructure, no new cost.
`k3d`, `kubectl` and `helm` are installed checksum-verified, the same pattern as `opa`/
`gitleaks`/Trivy elsewhere in this repo (pinned version, sha256 checked before the binary is
trusted) — not `curl | latest` the way the docker-compose/buildx plugins in the same bootstrap
script still do, an earlier gap this doesn't repeat. The install is in
`infra/modules/devbox/user_data.sh.tftpl` for reproducibility ("a stranger could reproduce it
from zero") — but the running devbox's `lifecycle { ignore_changes = [ami, user_data] }` means
this only takes effect on a future or recreated instance; the currently-running box was
updated by hand with the identical commands, verified against `terraform plan` showing no
changes (proving the ignore-changes claim rather than assuming it).

### One chart, built incrementally — this story covers Postgres, gateway, the correlate loop

`deploy/charts/kaval` deploys what Phase 2 already proved works: a `postgres` Deployment+PVC,
a `migrate` Job running the same `alembic upgrade head` compose.yaml's own `migrate` service
runs, the `gateway` API, and the `agent` running `kaval_agent.correlate --every 30` as a long-
running Deployment instead of a one-shot — the first time anything in this codebase runs
continuously inside real Kubernetes. `deploy/environments/local/values.yaml` holds the one
environment that exists today; staging and prod get their own values files in Phase 4, never a
template change specific to one environment (CLAUDE.md, ADR-0004).

**Images are built locally and imported, not pulled from a registry** — `docker compose build`
against the dev server's own Docker daemon (the same one k3d's nodes run on), then
`k3d image import`. A registry is Phase 4's problem (ECR); this environment is local by name
and by mechanism.

### Ollama stays out of this chart, deliberately — and so does /healthz as a k8s probe

`kaval_gateway`'s `/healthz` genuinely checks Ollama reachability and whether the configured
model is pulled, not just that the process is up. No environment's chart deploys Ollama yet,
because the component that would call it — `diagnose.py`, `escalate.py` — are still laptop-run
tools (ADR-0016's deployed-agent trigger design remains unresolved). Wiring a Kubernetes
liveness/readiness probe to `/healthz` today would crash-loop a gateway that is, in every way
that matters to the correlate loop this chart actually runs, working correctly. The gateway's
probes check `tcpSocket` on the app port instead — an honest "is it accepting connections",
not a false claim of full health. `/healthz` is still there to curl by hand (the chart's NOTES
say so), and still reports its honest `degraded` status with `ollama.ok: false` — found live,
not hypothesized, see Verification.

### The correlate Deployment has no Kubernetes RBAC yet, on purpose

`kaval_agent.correlate` only reads and writes Postgres rows; it doesn't call the Kubernetes API
at all today. The agent Deployment sets `automountServiceAccountToken: false` rather than
binding a Role it has no use for — real K8s-events-as-signals is what first gives this
component a reason to talk to the API server, and that later story is where its RBAC Role
belongs, scoped to exactly what it needs then, not provisioned speculatively now.

### Two real bugs, found live — both now guarded against automatically

1. **A `{{- -}}` trim pair silently deleted a YAML resource.** `agent.yaml`'s template had a
   long explanatory comment block immediately before `{{- $component := "agent" -}}`; the
   leading `{{-` ate the newline after the comment, and the trailing `-}}` ate the newline
   before `apiVersion: apps/v1` — merging them onto one line, which made `apiVersion: apps/v1`
   part of the `#` comment. `helm lint` did not catch this; only rendering the output and
   reading it did. Fixed (blank line + non-trimming `{{ }}`), and now caught automatically: CI
   gained a `helm` job that runs `helm template` through **kubeconform** (schema-validates
   rendered manifests offline, no cluster needed, checksum-verified download like every other
   CI tool) — confirmed to actually catch this exact bug by deliberately reintroducing it and
   watching kubeconform fail with `missing 'apiVersion' key`, not assumed to work.
2. **The migrate Job's hook was `pre-install`, which runs before anything else in the release
   exists — including the Postgres it waits for.** The first real `helm install` left the
   migrate Job's `wait-for-postgres` initContainer stuck at `Init:0/1` forever, with no
   postgres pod anywhere in `kubectl get pods` to wait for. Fixed: `post-install,pre-upgrade`
   (on a fresh install, Postgres already exists by the time this hook fires; on an upgrade,
   Postgres already exists from the prior release either way). No test catches hook-ordering
   bugs short of a real install — this one is in the lab's verification steps, not CI, for now.
3. **The agent Deployment crashed once on its very first tick**, racing Postgres's own startup
   — `correlate.py`'s `--every` loop doesn't catch a connection failure and retry, it exits.
   Kubernetes' own restart recovered it, which made the race easy to miss as "just a restart."
   Given the same `wait-for-postgres` initContainer the migrate Job already needed, confirmed
   on the next deploy: zero restarts.

## Consequences

**Easier.** The chart is the one place environments will differ going forward, exactly as
CLAUDE.md commits to — Phase 4's staging and prod values files add to this, they don't
restructure it. kubeconform now catches an entire class of template bug automatically, for
every future addition to this chart, not just this one fix.

**Harder.** Three more tools to keep current on the dev server (`k3d`, `kubectl`, `helm`) and
in CI (`helm`, `kubeconform`) — versions and checksums pinned by hand, like `opa`/`gitleaks`/
Trivy already are, with the same monthly-bump open thread.

**Accepted gap, documented not hidden:** `/healthz`'s Ollama check reads `ok: false` in every
k3d deployment today. That's correct given nothing deploys Ollama into the cluster yet, not a
bug to chase — revisit once ADR-0016's deployed-agent trigger design actually needs it.

**Revisit if:** real K8s-events-as-signals gives the agent a reason to talk to the Kubernetes
API (its RBAC Role is designed then, not now); Phase 4 adds staging/prod values files and ECR
image pulls; the deployed-agent trigger design (ADR-0016) resolves and Ollama needs to join
this chart for real.

## Verification

- `helm lint deploy/charts/kaval -f deploy/environments/local/values.yaml` — clean.
- `helm template ... | kubeconform -strict -kubernetes-version 1.35.5 -summary -` —
  8/8 resources valid. Deliberately reintroducing the trim-dash bug and re-running this exact
  command reproduces `missing 'apiVersion' key`, confirming the CI job actually catches it.
- `terraform plan` on `infra/envs/dev` after the `user_data.sh.tftpl` change shows **no
  changes** to the running instance — confirms `ignore_changes` behaves as claimed rather than
  assuming it.
- Live, on the real k3d cluster on the dev server: `helm install` then `helm upgrade` both
  succeed; `kubectl get pods` shows all four workloads `Running`, `1/1`, zero restarts after
  the fix. A real incident's synthetic signals were written via a one-off `kubectl run` using
  the collector image pointed at the in-cluster Postgres; the deployed agent's own logs show
  `opened    oom_killed:k8s:kaval-demo/checkout  high  6 signals` on its next tick — the
  correlate loop working for real, in real Kubernetes, not simulated. `kubectl port-forward` to
  the gateway confirms `/healthz` reports `{"status":"degraded", "postgres": {"ok": true,
  "detail": "migrated to <head revision>"}, "ollama": {"ok": false, "detail":
  "UnsupportedProtocol"}}` — exactly the documented, expected shape — and `/v1/incidents`
  returns the same incident the agent opened, proving the gateway reads the same database the
  deployed agent writes to.
