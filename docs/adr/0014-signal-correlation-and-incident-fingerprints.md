# ADR-0014 — Signal correlation: rules group signals into incidents, with readable fingerprints

- **Status:** Accepted
- **Date:** 2026-09-28
- **Deciders:** proposed by Claude; Roshan accepts or changes it at `KAV-39`'s UAT sign-off
- **Jira:** `KAV-39`

## Context

One failure produces many signals. The synthetic OOM scenario writes six: a memory warning, the
OOM kill, three restarts and an alert. The agent must treat those six as **one incident**. The same
failure coming back next week must get the **same fingerprint**, because the context builder (next
in Phase 2) finds past incidents by fingerprint and similarity, along with what was done and
whether it worked. The learn page names the trade-off: too broad, and unrelated failures merge; too
narrow, and every event is new and history never builds up.

The data model constrains the design. `incident` is append-only apart from `closed_at`, so an
incident's fingerprint and severity can't be corrected after it opens.

## Decision

**Correlation is plain code in the agent, not the model's job.** `kaval_agent.correlate` runs a
pass: it reads the signals not yet in an incident, groups them, and writes `incident` and
`incident_signal` rows in one transaction.

| Rule | Choice |
|---|---|
| **Subject** (what's broken) | Kubernetes: `namespace/workload`, with the pod's generated suffix removed (`checkout-7f9c4-x2klp` → `checkout`), so a restarted pod is still the same problem. AWS: the resource, e.g. `ebs/vol-0…` |
| **Cause** (what kind of broken) | Read from the signal's kind and payload: `oom_killed`, `exec_format`, `container_error`, `crashloop`, `memory_pressure`, `cost_spike`, `idle_volume`. The **most specific** cause in a group names the incident: a kill beats the restarts it causes |
| **Fingerprint** | `cause:domain:subject`, e.g. `oom_killed:k8s:kaval-demo/checkout`. Readable, not hashed |
| **Severity** | From rules: a kill or wrong-architecture image is high; restarts or a container error medium; a memory warning low; an alert's own severity mapped. The group's highest wins |
| **Group wait** | A new group opens 60 s after its first signal, as Alertmanager's `group_wait` does, so the cause has arrived before the fingerprint is fixed |
| **Quiet window** | An incident closes after 15 min (Kubernetes) or 48 h (AWS, whose data is daily and ~24 h late) with no new problem signal. `closed_at` is the last signal plus the window, so it doesn't depend on when the pass happened to run |
| **Recurrence** | A problem after the quiet window opens a **new** incident with the **same** fingerprint. That's how history builds up |
| **Not a problem** | A daily cost row within 3× the median of the week before, or a resolved alert. It stays unlinked. A kind nobody has classified yet becomes a low incident under its own name, so it isn't dropped silently |
| **Concurrency** | A Postgres transaction-level advisory lock: a second correlator skips its pass instead of opening the same incident twice |
| **Running** | `make correlate` runs one pass (Compose `tools` profile). The image also loops with `--every 30`, for when the agent is deployed in Phase 3 |

A new index, `ix_incident_signal_signal_id`, answers "is this signal linked yet?". The table's
primary key leads with `incident_id`, so it couldn't.

## Alternatives considered

- **Let the model group signals.** Rejected: it's slow, nondeterministic and untestable, and
  prompt injection through a log line could merge or split incidents. Grouping is bookkeeping,
  and bookkeeping should be exact.
- **A hashed fingerprint** (Alertmanager's label hash). Rejected for now. A readable key costs
  nothing here, works on the phone without a lookup, and holds no secrets: subjects are
  workload names and resource ids, never ARNs. If a subject ever could carry an account id, hash
  that part.
- **Workload only, no cause, in the fingerprint.** Rejected: an OOM and a bad image on the same
  workload need different fixes, and their histories shouldn't mix.
- **Open on the first signal.** Rejected: the fingerprint is fixed at opening, and the first
  signal is usually a symptom (a memory warning, a restart), not the cause.
- **Update the fingerprint as signals arrive.** Rejected: it breaks the append-only rule that
  makes the tables an audit trail.
- **A long-running loop in Compose.** Deferred: in the inner loop you want to see each pass's
  output, and a background loop would claim the signals first.

## Consequences

- `/v1/incidents` now has data after `make signals` + `make correlate`.
- The agent is a real component: its own image (arm64, non-root, `0.1.0`), in the CI image matrix
  and in Dependabot.
- Signals from different causes on one workload, arriving while its incident is open, join that
  incident under its first cause. That's accepted: it's the same workload and still broken, and
  the incident's signals keep every cause for the context builder to read.
- Pod-name parsing is a heuristic until Phase 3's real collector records the owner
  (`ownerReferences`). The owner should then be the subject.
- The rules are tuned by the Phase 2 eval harness: the golden incidents check that each scenario
  correlates to the incident a person would name.
