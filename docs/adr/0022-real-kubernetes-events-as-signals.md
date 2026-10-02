# ADR-0022 — Real Kubernetes events as signals

- **Status:** Accepted — built, deployed, verified live
- **Date:** 2026-10-01
- **Deciders:** Roshan

## Context

Every signal Kaval has ever reacted to, through `KAV-47`, was typed in by hand — a fake
incident from `kaval_collector.synthetic`, or a one-off row written directly into Postgres for
a demo. That was the right call for Phases 1–2 (nothing downstream needed a real cluster yet),
but it means Kaval has never actually *watched* anything. `services/collector/` held only the
synthetic generator. This story gives it a second source: real Kubernetes events, polled
continuously, written as the same `signal` rows everything downstream already understands.

`docs/learn/phase-3-kubernetes-local.md` already designed this, in its "Kubernetes events as
signal, and their traps" section, before any of it was built — three properties to design
around: events expire (~1 h retention), they deduplicate with a `count` rather than repeating
as new objects, and they aren't guaranteed to arrive. This ADR is that design, built.

## Decision

### A poll loop, not a watch stream

`kaval_collector.k8s_events` lists `Warning` events on a timer (`--every 15`, the same CLI
shape `kaval_agent.correlate` and `kaval_executor.executor` already use), rather than holding
a persistent Kubernetes `watch` connection. Simpler, and more robust to a dropped connection —
a missed poll just means the next one still sees the event, as long as it hasn't aged out of
the ~1 h retention window this polls far more often than. The one-pass-per-tick shape is also
the only loop pattern anything else in this codebase uses; a `watch` stream would be a second,
different concurrency model for no benefit this story needs.

### Count-based de-duplication, against the database itself

Kubernetes doesn't emit a new Event object per occurrence of the same problem — it bumps the
existing object's `count`. Two wrong ways to handle that, both named as mistakes in the learn
page already written: write a new signal on every poll regardless (floods the table with
identical rows for an event nobody has looked at since the last poll), or write exactly one
signal ever, keyed off the event object (a pod crash-looping for an hour produces one signal,
and `kaval_agent.correlate`'s `QUIET` window closes the incident while the real problem is
still happening).

`poll()` does neither: every signal it writes carries the source event's own `uid` in its
`value`, and before writing a new one it queries the most recent signal already written for
that `uid` (`Signal.value.contains({"event_uid": uid})`, ordered newest first) and compares
counts. A new signal is written only when the real count has grown. A crash-looping pod keeps
producing fresh signals, keeping the incident's `last_seen` extending; a one-off event produces
exactly one row, ever.

### The value shape matches `kaval_collector.synthetic`'s exactly, on purpose

`synthetic.py`'s `_event()` helper already builds `{type, reason, message, count,
involvedObject: {kind, namespace, name}}` — written, in Phase 1, to look like a real
Kubernetes Event *before* a real one ever reached this codebase. This story's real events are
written in the identical shape (plus one addition, `event_uid`, for the de-dup query above).
The consequence: `kaval_agent.correlate.cause_of()` needed **zero changes** — it already reads
`kind` and `value.reason`/`value.message` the same way regardless of which collector wrote the
row, which is exactly what a `synthetic: true`-style distinction is supposed to buy and what
Phase 1's own design committed to.

### Reason -> `kind`: a small table, with an honest fallback

`kind_for()` maps the handful of real Kubernetes event reasons `cause_of()` specifically
recognises (`OOMKilled` -> `pod_oom_killed`, `BackOff` -> `pod_back_off`, an `Error` whose
message contains "exec format error" -> `container_exited`) and snake-cases everything else
(`FailedScheduling` -> `failed_scheduling`). Nothing is dropped for being unrecognised —
`cause_of()`'s own module docstring already says "a kind nobody has classified yet still
becomes an incident," at low severity rather than silently.

### Scope: one namespace, `Pod`-involved events only

Watches `.Values.executor.demoNamespace` (`kaval-demo`) — the one namespace with anything
worth watching today, the same one `KAV-47`'s executor acts on. Only events whose
`involvedObject.kind == "Pod"` become signals: `kaval_agent.correlate.subject()` already
expects a `namespace/workload` target derived from a pod name, and a `Node`- or
`Deployment`-involved event would need a different target shape this module doesn't produce.
Left for whenever that's actually needed, the same "code and RBAC grow together" call
`KAV-47` made for the executor, not provisioned speculatively now.

### RBAC: narrower than what was already documented, for the same reason as `KAV-47`

The collector's Role grants `list`, `watch` on `events` — not `get` (nothing here ever fetches
one named event by name), not `pods`/`deployments` (the Event objects this reads already carry
everything needed, in their own `involvedObject` field, with no second lookup). This is
narrower than `docs/learn/phase-3-kubernetes-local.md`'s RBAC table, which gives `collector`
"the same read-only set" as the agent (`get`, `list`, `watch` on pods, events, deployments) —
the same deliberate deviation `KAV-47`'s ADR-0021 already explains and defers reconciling to
the page's own `Written from: experience` flip.

Its ServiceAccount lives in the release's own namespace; its Role is granted in `kaval-demo`
only — the identical namespace split `KAV-47` established for the executor, now a repeated
pattern rather than a one-off.

## Consequences

**Easier.** The exit-gate demo no longer needs a hand-written signal row: breaking something
for real in `kaval-demo` is now enough on its own to start the loop. Any future real signal
source (Prometheus, Alertmanager, Cost Explorer) has a working example of "write the same
shape Phase 1 already committed to" to follow.

**Harder.** A second poll loop to keep running and watch resource usage for, alongside the
agent and the executor — three continuously-looping Deployments now, up from one after
`KAV-46`.

**Accepted gap, documented not hidden:** only `Pod`-involved events in one namespace become
signals. Node-level and Deployment-level problems (node pressure, a Deployment stuck
progressing) produce real Kubernetes events today that this collector silently skips — not a
bug, a scope line, revisited if something needs it.

**Revisit if:** a `Node`- or `Deployment`-shaped incident becomes something worth reacting to
(the target-shape question above needs an answer first); Prometheus arrives as a second real
source and the two need to share more than the `signal` table; `docs/learn/
phase-3-kubernetes-local.md`'s RBAC table is reconciled against what's actually granted, at
the end of Phase 3.

## Verification

- `make lint` and `make test` green against the real dev-server Postgres
  (`KAVAL_REQUIRE_DB=1`) — including `kind_for()`'s mapping table and `poll()`'s de-dup logic
  against a fake `CoreV1Api` stand-in (the same "fake the Kubernetes client, not the cluster"
  shape `kaval_executor`'s own tests use).
- `helm lint`/`helm template | kubeconform` clean — the chart now renders 17/17 resources
  (the collector's `ServiceAccount`, `Role`, `RoleBinding` and `Deployment`, alongside the 13
  from `KAV-46`/`KAV-47`).
- Live, on the real k3d cluster: `kubectl auth can-i list events --as=...:collector -n
  kaval-demo` -> `yes`; the same check for `delete pods`, and for `list events` in the
  collector's own namespace -> `no`, `no` — the collector can watch one namespace, never act
  anywhere, proven the same way `KAV-47`'s executor checks were.
- **The real exit-gate demo, with no hand-written signal:** a real `busybox:1.36` pod set to
  `exit 1` immediately, in `kaval-demo`. The deployed collector's own log, unattended:
  `pod_back_off kaval-demo/crashy count=2` through `count=5`, climbing with every real
  restart — the count-based de-dup working against a real event, not a fake one. The deployed
  agent's own log, same window, no prompting: `opened crashloop:k8s:kaval-demo/crashy medium 3
  signals` then `attached 1 signals to 1 open incident(s)`. `GET /v1/incidents/{id}` through
  the gateway's own read API read back one open incident with five real `pod_back_off`
  signals, counts 2 through 6, each timestamped from the real event's own `lastTimestamp` —
  the complete chain, with not one row written by hand. Full transcript: Lab 17.
