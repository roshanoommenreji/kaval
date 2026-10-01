# ADR-0021 — The executor, its scoped RBAC, and the gateway's approval write path

- **Status:** Accepted — built, deployed, verified live
- **Date:** 2026-10-01
- **Deciders:** Roshan

## Context

`KAV-46` deployed the part of the stack that already existed — Postgres, gateway, the
correlate loop — to a real k3d cluster. Phase 3's actual exit gate, "kill a pod locally →
agent proposes → you approve → executor fixes it", still had three missing pieces: nothing
could change anything (`services/executor/` held a `.gitkeep`), nothing could record a human's
approval (the gateway's own module docstring said so explicitly: "the gateway's writes...
arrive with the approval flow; until then nothing here can change the audit trail"), and
nothing stood between an approved action and the cluster except trust in whatever classified
it. This story builds all three together, because they only mean anything as one path.

This is also the first time CLAUDE.md constraint 3 — "the agent never gets write access" —
has anything on the other side of it to point at. Before this, the privilege split was a
diagram. Now it's a RoleBinding.

## Decision

### The executor's RBAC is scoped to a namespace it doesn't live in

`kaval_executor`'s ServiceAccount lives in the release's own namespace, alongside
postgres/gateway/agent. Its Role and RoleBinding are granted in a **separate** namespace —
`kaval-demo` (`.Values.executor.demoNamespace`), created by the chart, intended for workloads
the executor acts on, never for Kaval's own components.

The consequence this buys, beyond "scoped to the app namespace rather than cluster-wide"
(already the agent's and the executor's documented design in
`docs/learn/phase-3-kubernetes-local.md`): the executor has **zero** RBAC grant in its own
release's namespace. It cannot touch the very postgres/gateway/agent pods it's deployed next
to, let alone `kube-system` — not because application code refrains, but because no binding
grants it there at all. Verified live, not asserted:

```
$ kubectl auth can-i delete pods --as=system:serviceaccount:default:kaval-local-executor -n kaval-demo
yes
$ kubectl auth can-i delete pods --as=system:serviceaccount:default:kaval-local-executor -n default
no
$ kubectl auth can-i delete pods --as=system:serviceaccount:default:kaval-local-executor -n kube-system
no
```

### RBAC grants exactly the verbs the code calls today, nothing speculative

The Role's only rule is `get`, `delete` on `pods`. Not `list`/`watch` (nothing enumerates or
watches), not `patch` on `deployments` (no handler calls it — a future `scale_deployment`
action gets its own RBAC grant in the same PR that adds the handler, not before). This is a
narrower grant than `docs/learn/phase-3-kubernetes-local.md`'s RBAC table describes, which was
written `Written from: theory` before this story — a deliberate deviation, not an oversight;
reconciling that page is the same task as flipping its marker to `experience`, which waits for
the rest of Phase 3 (real K8s-events-as-signals, the staging/prod split, the rollback drill),
not this story alone.

### The second policy check is real code, not a comment about intent

`policy/README.md` has said "evaluated twice" since `KAV-42` (ADR-0017), with the executor
named as the second caller that didn't exist yet. It exists now: `kaval_executor.executor`
calls the identical `kaval_agent.policy.classify()` — same `opa` binary, same `policy/` bundle,
bundled into the executor's own image — from the action's own `type`/`blast_radius`/
`reversible` and its proposal's `confidence`, **never from the stored `policy_class`**,
immediately before acting. A `never` on that fresh check refuses and records why; so does an
`ask` with no real human approval attached. Neither raises, neither silently drops — both write
exactly one `execution` row with `status=skipped` and the reason, so the audit trail shows a
refusal happened rather than showing nothing.

Verified live by simulating the failure this exists for: an action typed `delete_pvc`
(matches `policy/policy.rego`'s `never` keyword list), stored with `policy_class=ask`, with an
`approved` `Decision` attached **directly in the database — bypassing the gateway's own
endpoint entirely**, so the gateway's own refusal of this case never even ran. The deployed
executor's own next pass:

```
skipped   delete_pvc   4120b028-221d-4ba1-a179-c553b51c2c99  refused: policy re-check says
'never' (stored class was ask)
```

Nothing was touched. This is what "makes a compromised agent harmless" (`policy/README.md`)
means concretely: not that the gateway is careful, but that the executor doesn't need the
gateway to have been.

### `stdout` is redacted before the row is ever written, not after

`kaval_executor.redact.redact()` runs inside `_record()`, the one function that ever
constructs an `Execution` row — not a step a caller could forget, and not a pass over
already-written data. Two kinds of pattern: well-known credential shapes (AWS account ids,
ARNs, access key ids, bearer tokens, JWTs, PEM blocks), specific enough not to eat pod names
or UUIDs the audit trail needs to stay readable; and `kubectl ... -o yaml`-shaped `data:`/
`stringData:` blocks, blanked by indentation-tracking rather than a full YAML parse, since the
only input this ever sees is `kubectl` output. The second pattern also blanks a ConfigMap's
`data:` (not actually secret) — deliberate over-redaction, the same bias
`policy/policy.rego`'s keyword match documents for the identical reason: a blanked word in a
log costs less than a secret that reaches a column the gateway serves to a phone. This closes
the gap ADR-0005 named without building: redacting only when copying prod data to staging
would mean prod itself held secrets in a queryable column in the meantime.

### The gateway's one write, and why it isn't the security boundary

`POST /v1/actions/{id}/decisions` is the gateway's first write path — everything else stays on
`read_session`'s database-enforced `READ ONLY` transaction. It refuses a second decision on an
already-decided action (the database's own `UniqueConstraint` would refuse it anyway; the
endpoint checks first so the failure is a clear 409, not a leaked `IntegrityError`), and it
refuses to record *any* verdict on a `policy_class=never` action — a human cannot approve one
through this endpoint, matching ADR-0006 rule 1's "regardless of what anyone says."

That refusal is convenience, not the enforcement boundary, and the executor's own re-check
proves it: even with the gateway's refusal bypassed entirely (the live test above wrote the
`Decision` row directly), nothing happened. The real boundary is two independent checks that
happen to agree, not one check asked twice.

### `scripts/ops/approve.py` is the Phase-3 stand-in for the mobile app's approval screen

A human still decides — Phase 5 swaps the terminal for a phone and swipe-to-approve for a
`POST`, but the same endpoint, the same audit row, the same executor underneath. Stdlib-only
(`argparse`, `json`, `urllib`), so it runs anywhere Python exists without a venv — including
directly against a cluster reached only through `kubectl port-forward`, which is how it was
actually used for the live verification below.

### Two more bugs, found live

1. **`k3d image import` doesn't restart anything.** Rebuilding the gateway image (its code
   changed for the new decision endpoint) and re-importing it into k3d left the running
   gateway pod serving the *old* image — same tag (`kaval/gateway:dev`), same `Deployment`
   spec text, so Helm saw nothing to change and Kubernetes saw no reason to reschedule the
   pod. The first live approval attempt got a real `404 Not Found` from a gateway that
   genuinely didn't have the route yet. Fixed with `kubectl rollout restart
   deployment/kaval-local-gateway`; the general lesson (a `:dev`-tagged image's *content*
   changing is invisible to both Helm and Kubernetes unless something is told to reschedule)
   is written into Lab 16's steps so it isn't rediscovered next time.
2. **Running a second process inside the executor's own 128Mi limit OOM-killed the container.**
   Seeding a demo action via `kubectl exec <executor-pod> -- python seed.py` — a second Python
   process sharing the main loop's cgroup — got the whole container `OOMKilled`
   (`lastState.terminated.reason`, confirmed, not guessed) on the first attempt, and again
   after removing the script's own `opa eval` subprocess call. The main loop alone fits
   comfortably in 128Mi (it had run for hours before and after with zero restarts); a second,
   unrelated process sharing that limit does not. Worked around for the live verification with
   a throwaway, unconstrained `kubectl run` pod running the same image — not a change to the
   chart's real resource limits, because the limits are sized for the executor's actual job,
   and an ad hoc diagnostic script competing for the same cgroup isn't it.

## Consequences

**Easier.** The security story stops being a diagram: `kubectl auth can-i` and a database row
are now the proof, not a claim. A future `scale_deployment` (or any new action type) has an
exact template to follow — add the handler to `k8s.SUPPORTED_ACTIONS`, add its one RBAC verb in
the same PR, nothing provisioned ahead of the code that uses it.

**Harder.** Every new action type is now two changes in lockstep (handler + RBAC), not one —
a deliberate cost, not an oversight; see "RBAC grants exactly the verbs the code calls today."

**Accepted gap, documented not hidden:** `docs/learn/phase-3-kubernetes-local.md`'s RBAC table
still describes a broader grant (`patch` on pods and deployments) than what's actually bound
today. Reconciling it waits for the page's `Written from: experience` flip at the end of
Phase 3, not this story.

**Revisit if:** a second action type needs its own RBAC verb (extend the Role, not replace it);
`promotions.json` ever gains its first entry (the `_refusal` drift path — stored `auto`, fresh
check disagrees — stops being a defensive case that can't currently happen and starts being
one that occasionally will); the mobile app (Phase 5) replaces `scripts/ops/approve.py`, at
which point this ADR's "stand-in" framing is the thing to update, not the endpoint itself.

## Verification

- `make lint` and `make test` green — 270 passed, 14 skipped (the pre-existing `opa`-on-PATH
  skip guard, laptop-only) against the real dev-server Postgres (`KAVAL_REQUIRE_DB=1`).
- `helm lint` clean; `helm template ... | kubeconform -strict -summary -` — 13/13 resources
  valid on the real devbox, with the real `kubeconform` CI uses.
- Live, on the real k3d cluster: `helm upgrade` to revision 3 — `kaval-local-executor` comes
  up `1/1 Running`, zero restarts (the `wait-for-postgres` pattern from `KAV-46` worked on the
  first try this time). The four RBAC `can-i` checks above, against the real API server.
- **The full happy path, live, unattended after the approval:** a real incident/proposal/
  action seeded with `target=kaval-demo/checkout-<pod>`; approved through the real gateway
  `POST /v1/actions/{id}/decisions` via `scripts/ops/approve.py`, reached through
  `kubectl port-forward`; the **already-running, continuously-looping** deployed executor
  picked it up on its own next `--every 15` pass — no manual trigger — and logged
  `success  restart_pod  <action-id>  deleted pod kaval-demo/checkout-<pod> (uid ..., was
  Running, owner=ReplicaSet)`. `kubectl get pods -n kaval-demo` showed a new pod, new name,
  `RESTARTS 0` — the ReplicaSet healing it, not the executor itself. `GET
  /v1/incidents/{id}` through the gateway's own read API returned the complete timeline —
  incident → proposal → action → decision → execution, every field populated, `before_state`
  and `after_state` both present — reading back exactly the row the executor wrote, through
  the same API the phone will eventually use.
- **The refusal path, live:** see "The second policy check is real code" above —
  a `never`-class action, approval bypassed directly in the database, refused by the deployed
  executor's own independent re-check, nothing touched.
