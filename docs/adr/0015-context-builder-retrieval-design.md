# ADR-0015 — Context builder: three retrieval sources, and what each one actually needs

- **Status:** Accepted
- **Date:** 2026-09-28
- **Deciders:** proposed by Claude; Roshan accepts or changes it at `KAV-40`'s UAT sign-off
- **Jira:** `KAV-40`

## Context

Before the agent can ask a model to diagnose an incident, it needs to assemble what a person
on call would read first. `docs/learn/phase-2-the-agent-loop.md`'s "RAG, and why not
fine-tuning" section already named the three sources: matching runbooks, similar past
incidents with their outcomes, and recent changes to the affected workload. None of it
existed yet — `KAV-39` built the incidents this context is *for*, not the context itself.

Three separate design questions turned out to need three separate answers, not one shared
mechanism:

1. **Runbooks** are prose. Finding the right section needs semantic similarity — a keyword
   match would miss "container_memory_near_limit" being about the same problem as "the pod
   keeps OOMing".
2. **Past incidents** already carry a precise, structured description: the fingerprint
   (`cause:domain:subject`, [ADR-0014](0014-signal-correlation-and-incident-fingerprints.md)).
   Two incidents with the same cause and domain *are* the same kind of failure — no similarity
   score needed to establish that, and a score would be a strictly worse signal than the exact
   match already available.
3. **Recent changes** ran into this project's own security boundary: the root
   `.dockerignore` deliberately keeps `.git` off every image (documented there since Phase 0),
   and there is no real Kubernetes deployment history yet — Phase 3 wires that. So "recent
   changes" in Phase 2 can only be a laptop-side proxy, not a service the deployed agent calls.

## Decision

### Runbooks: chunk by `## ` section, embed with all-minilm, retrieve by cosine similarity

- `kaval_agent.runbooks.chunk_runbook` splits each file in `docs/runbooks/` on its `## `
  headings (Signals, Likely causes, Diagnosis, Remediation, Do not, ...) — the format
  `docs/runbooks/README.md` already specifies. One chunk per section: coarser than a
  paragraph, finer than a whole runbook, matching how a person actually reads one.
- **all-minilm** (384-dim, ~45 MB, CPU-only) embeds both the chunks and each incident's query
  text. Runbook chunks and incident descriptions are short — a heading's worth of text — so a
  compact model is enough, and `OLLAMA_MAX_LOADED_MODELS=1` means it and the chat model are
  never resident together anyway; this is a disk and latency choice, not a memory one.
- `kaval_agent.index_runbooks` syncs `docs/runbooks/*.md` into a new `runbook_chunk` table
  (pgvector), matched by `(path, heading)`, re-embedding only a chunk whose content hash
  changed, deleting rows for headings or files that no longer exist. It's a **laptop-run
  tool** (`make index-runbooks`), like `jira-sync.py` — `docs/runbooks/` isn't in the agent's
  Docker image, so a runbook edit needs re-indexing before it's found, the same way a code
  change needs a rebuild.
- **The relevance cutoff is measured, not guessed.** On the dev server, against the one
  runbook that exists today: a genuine match (a database-connection query against "database
  lost or corrupted") scored **0.69**; two unrelated queries (an OOM crashloop, an idle
  volume) against the *same* runbook scored **0.38–0.40** — all-minilm's floor for any two
  short technical chunks sharing vocabulary ("container", "kaval", ...), not real relevance.
  `MIN_RUNBOOK_SCORE = 0.5` sits in that gap with headroom on both sides.

### Past incidents: exact structured match, not embeddings

`kaval_agent.context.similar_incidents` finds closed incidents sharing the current one's
**cause and domain** (`func.split_part` on the fingerprint — Postgres-specific, which this
codebase already embraces via JSONB and pgvector), an exact fingerprint match (a recurrence)
ranked first, most recent first, left-joined with the latest `outcome` if one was measured.

No embedding is computed or stored for an incident. The fingerprint is already the more
precise signal for "is this the same kind of failure" than a similarity score would be, and
skipping it keeps `incident` free of another column to keep in sync. This is a **narrower
scope than pgvector-based incident similarity**, which the architecture diagram's "Seen
before?" routing node (local model vs. Bedrock) will need later — that's a different question
("have I handled anything *like* this, even loosely") from this one ("has *this exact kind* of
failure happened"), and gets its own design when that task starts.

### Recent changes: a laptop-only, honestly-a-proxy git log

`kaval_agent.recent_changes.for_workload` maps a workload name to one of this repo's own
service folders (`gateway`, `collector`, `agent`, `executor`, `shared`) and reads `git log`
against it. It:

- **runs from the laptop, not inside the agent's Docker image** — `.git` is deliberately never
  part of what leaves the laptop (`.dockerignore`'s allowlist, unchanged), and that boundary is
  worth more than this one feature;
- **degrades honestly** to `available: false` when git isn't reachable, and to
  `service: None` when the workload isn't one of this repo's services (the synthetic
  scenarios' fictional `checkout` workload, for instance, correctly returns "unmapped");
- **is a proxy, not the real thing.** "What changed recently" should mean recent deployments
  the cluster actually saw, which doesn't exist as data yet. When Phase 3's collector starts
  emitting real deployment signals, this module is **replaced, not extended** — the interface
  `build_context()` calls (`changes: RecentChanges | None`) doesn't need to change, only what
  supplies it.

### Assembly

`kaval_agent.context.build_context(session, incident, embed_fn=embed_one, changes=None)`
orchestrates all three and returns a `Context` whose `.render()` produces one prompt-ready
text block — 3–5 runbook matches, 2–3 similar incidents, recent changes if supplied. Everything
here reads; nothing writes, same as correlation. `python -m kaval_agent.context <id>
[--with-changes]` (`make context INCIDENT=<uuid>`) prints it for inspection — the step that
actually calls the model for a diagnosis is a later Phase 2 task, not this one.

## Alternatives considered

- **Embedding every incident, stored on the row.** Rejected for this task: it would mean
  either coupling correlation (pure, fast, no network call by ADR-0014's own design) to an
  embedding call, or computing embeddings lazily and updating `incident` rows after the fact —
  breaking the "nothing but `closed_at`" append-only invariant `models.py` documents. Deferred
  to the "seen before?" routing task, which has a genuinely different question to answer.
- **Baking `docs/runbooks/` and `.git` into the agent's Docker image**, so retrieval could run
  fully inside the deployed container. Rejected: it would mean either punching a hole in the
  `.dockerignore` security boundary (for `.git`) or duplicating the docs into the image and
  keeping two copies in sync (for `docs/runbooks/`) — real complexity for a feature that, in
  Phase 2's actual state (no real deploy or diagnosis pipeline calling this yet), has no
  deployed consumer today. `index_runbooks`/`context` run from the laptop, like every other
  Jira/Confluence/dashboard tool already does.
- **A `LIKE`-based fingerprint prefix match** instead of `func.split_part`. Rejected: cause and
  domain names come from `_snake()` and can contain `_`, a `LIKE` wildcard character that would
  need explicit escaping to be correct; `split_part` sidesteps the whole class of bug.
- **A synthetic "deploy" signal source**, so recent changes could flow through the normal
  `signal` table like everything else. Rejected as scope creep for this task — it's the right
  eventual shape (Phase 3's real collector), not something worth inventing a fake version of
  now.

## Consequences

- `runbook_chunk` is the first table in this project that isn't part of the append-only spine.
  Its docstring says so explicitly, and `test_all_spine_tables_present` was changed from exact
  equality to a subset check — still a tripwire for a dropped spine table, no longer a false
  alarm for a legitimate reference table.
- `pgvector` is now a **core** dependency (every image imports `kaval_shared.models`, which
  imports it); `httpx` gained a new `agent` extra for the laptop-run tools only. Neither needed
  a Dockerfile change — `correlate.py`, the agent's only current entrypoint, still makes no
  network call.
- **The relevance cutoff needs recalibrating once a second runbook exists.** One data point
  can't rule out the genuine-match/noise gap being narrower for some other kind of failure.
- **Recent changes stay laptop-only until Phase 3.** The deployed agent's future diagnosis step
  can call `build_context()` with `changes=None` in the meantime — an honest "not checked",
  which `Context.render()` shows differently from "checked, found nothing".
