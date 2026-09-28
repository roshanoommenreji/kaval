# Lab 10 — The context builder: runbooks, history, and recent changes

**Phase:** 2 · **Time:** ~45 min · **Cost:** about 1–2 cents of dev-server time

Before the agent can ask a model to diagnose anything, it assembles what a person on call
would read first. This lab builds and runs that assembly on real incidents. The design is
[ADR-0015](../adr/0015-context-builder-retrieval-design.md).

## Prerequisites

- [Lab 09](lab-09-signal-correlation.md) done: `make correlate` produces incidents to build
  context for
- `make dev` running, and `make dev-tunnel` in its own terminal

---

## Step 1 — Pull the embedding model

A second, much smaller model than the chat one: **all-minilm**, 384 dimensions, ~45 MB.

```bash
make pull-embed-model
```

Only one model is resident in Ollama at a time (`OLLAMA_MAX_LOADED_MODELS=1`), so this costs
disk, not RAM — it swaps in only when something actually calls it.

## Step 2 — Migrate, then index the runbooks

```bash
make migrate               # adds runbook_chunk and enables the pgvector extension
make index-runbooks
```

```
created    6
  restore-from-backup.md — Signals
  restore-from-backup.md — Likely causes
  restore-from-backup.md — Diagnosis
  restore-from-backup.md — Remediation
  restore-from-backup.md — Do not
  restore-from-backup.md — Related
```

Run it again:

```
unchanged  6
  ...
nothing changed
```

One chunk per `## ` heading. Edit one line of `docs/runbooks/restore-from-backup.md` and
re-run: only that chunk re-embeds and shows as `updated`; the rest still say `unchanged`. That's
the proof the sync is content-addressed, not a blind re-embed of everything.

**Why this doesn't run inside the agent's Docker image:** `docs/runbooks/` isn't in it —
the root `.dockerignore` allowlist only admits `pyproject.toml`, `uv.lock`, `services/` and
`migrations/`. `index-runbooks` is a laptop-run tool, like `jira-sync.py`.

## Step 3 — Build context for an incident that has no matching runbook

```bash
make signals SCENARIO=oom-crashloop
make correlate
```

Take the incident id it printed (or read it from `/v1/incidents?status=open`), then:

```bash
make context INCIDENT=<id>
```

```
INCIDENT: oom killed on kaval-demo/checkout (k8s). Container checkout exceeded its memory
limit (256Mi) and was killed. Back-off restarting failed container checkout.
KubePodCrashLooping

RUNBOOKS:
  (no runbook section scored above the relevance cutoff)

SIMILAR PAST INCIDENTS:
  (none — first time this kind of failure has been seen)

RECENT CHANGES:
  (unmapped workload)
```

The only runbook today is about a lost database — correctly irrelevant to an OOM, and the
retrieval says so honestly rather than forcing a weak match. `checkout` is the synthetic
scenario's fictional workload, not a real service this repo builds, so recent changes
correctly says `unmapped workload` rather than pretending to have checked.

## Step 4 — Same command, a workload that *is* real

`gateway` is an actual service in this repo. Trigger the scenario that uses it:

```bash
make signals SCENARIO=exec-format
make correlate
make context INCIDENT=<the new incident's id>
```

```
RECENT CHANGES:
- 78333038f5 2026-09-28 feat: KAV-39 signal correlation groups signals into fingerprinted incidents
- 6b3741966d 2026-09-28 feat: KAV-35 component versions, reported everywhere and checked in CI
  ...
```

Real commits touching `services/gateway/` in the last 14 days, newest first — pulled from this
repo's own `git log`, which only works because this command runs on the laptop, not in a
container.

## Step 5 — See a real recurrence

Trigger `exec-format` a second time (or wait for one already in the database from an earlier
session) and build context for it:

```
SIMILAR PAST INCIDENTS:
- exec_format:k8s:kaval-demo/gateway opened 2026-09-27: outcome not yet measured
```

The older incident, correctly excluded from being compared against itself, shown with its own
opened date. "Outcome not yet measured" is honest: nothing writes to the `outcome` table yet —
that's a later Phase 2 task.

---

## Done when

- [ ] `make pull-embed-model` then `make index-runbooks` twice: the second run is all
      `unchanged`
- [ ] Editing one runbook heading and re-indexing shows exactly that chunk as `updated`
- [ ] `make context` on an OOM incident shows no runbook match and `unmapped workload`
- [ ] `make context` on an exec-format incident (`gateway`) shows real recent commits
- [ ] A recurrence shows the earlier incident under **similar past incidents**
- [ ] KAV-40 signed off: `jira-sync.py uat KAV-40 pass --env dev`

## What went wrong, and why (2026-09-28)

- **The relevance cutoff was a guess before it was measured, and the guess was wrong.**
  Written first at 0.35 from intuition; measured on the dev server, an *unrelated* query
  against the only runbook scored 0.364 — just past that "cutoff". Two more real queries
  (an OOM, an idle volume) against the same runbook measured 0.38–0.40; a genuinely matching
  query measured 0.69. The real gap sits well above the guess. Fixed to 0.5, from the
  measurement, not the intuition — see ADR-0015's exact numbers.
- **The runbook-sync and retrieval tests first failed against a shared dev database**, not
  because the code was wrong: `sync_runbooks`'s own logic (delete whatever's not in this run's
  file set) correctly saw the real, already-indexed `restore-from-backup.md` rows from Step 2
  as stale, because the test's temp directory didn't include them. Each database-backed test
  now clears `runbook_chunk` inside its own rolled-back transaction first, so it never depends
  on — or disturbs — whatever a real indexing run left behind.
- **A test helper silently shadowed its own default.** `_incident(fingerprint, **kw)` set
  `opened_at=AT` unconditionally, so a call also passing `opened_at=...` in `kw` raised a
  `TypeError` about a duplicate keyword argument instead of doing what it looked like it did.
  Fixed with `kw.setdefault("opened_at", AT)`.
