# ADR-0016 — Schema-enforced proposal output: constrained decoding, then re-validated anyway

- **Status:** Accepted
- **Date:** 2026-09-28
- **Deciders:** proposed by Claude; Roshan accepts or changes it at `KAV-41`'s UAT sign-off
- **Jira:** `KAV-41`

## Context

`KAV-40` built what a person on call would read before diagnosing anything. Nothing had
actually asked the model yet. `docs/learn/phase-2-the-agent-loop.md` had already named the
shape of the answer before any of this was built: "the model returns validated JSON or the
attempt fails" — not a best-effort parse of whatever comes back.

Two things needed deciding that weren't obvious from that sentence alone:

1. **Where does enforcement actually happen** — in the model's decoding, in a validator after
   the fact, or both? Ollama (0.34, already pinned by `compose.yaml`) supports structured
   outputs: a `format` field on `/api/chat` can carry a JSON Schema, and the model's decoding
   is constrained to only ever produce tokens that keep the output on-schema. That sounds like
   it makes a second check redundant.
2. **What happens to `Action.policy_class`**, which is `NOT NULL` on the spine's `action`
   table (`services/shared/kaval_shared/models.py`, present since the very first data-model
   work) but has no classifier yet — the policy engine (`auto`/`ask`/`never`) is the *next*
   story on the roadmap, not this one.

## Decision

### Constrained decoding *and* Pydantic validation, not one or the other

`kaval_agent.schema.Diagnosis` (a Pydantic model, `extra="forbid"` throughout) is the single
source of truth for the shape. `kaval_agent.diagnose.diagnose()`:

1. Sends `Diagnosis.model_json_schema()` as Ollama's `format`, so the model's own decoding is
   steered toward the right field names, types, enum values and numeric bounds.
2. Re-validates the parsed JSON against the same `Diagnosis` model regardless.

Trusting step 1 alone was rejected. Constrained decoding is a property of *this* Ollama
version and *this* model's tokenizer, not a contract Pydantic itself makes any promise about;
the day either one changes, a subtly-wrong reply reaches the database with nobody having
checked it. This is the same reasoning `embeddings.py` already uses for checking a returned
vector's dimension instead of trusting `/api/embed`'s contract — see
[ADR-0015](0015-context-builder-retrieval-design.md). Two layers cost one extra `try/except`
and catch a real, not hypothetical, class of drift.

### One corrective retry, then fail closed

A first reply that fails validation gets exactly one more turn: the validation error is
appended to the conversation (`"That did not match the required schema: ... Reply again with
corrected JSON only"`) and the model retries once. A second failure raises `DiagnosisError`
and **writes nothing** — no partial proposal, no proposal with a placeholder action.

Chosen over zero retries (rejected: a 1B model's most common failure mode is small —
`"reversible": "yes"` instead of `true` — and a bare rejection would throw away a nearly-right
answer for no benefit) and over unlimited retries (rejected: nothing stops a model that
structurally cannot produce a valid reply for a given incident from looping forever; two
attempts is enough to fix a typo-class mistake and not enough to hide a real failure to
diagnose).

### The model never sets its own policy class

`schema.DiagnosisAction` has no `policy_class` field — not defaulted, not optional, simply
absent from what the model is even asked for. `write_proposal` hardcodes every
`Action.policy_class` to `PolicyClass.ask`, the same value `.env.example`'s
`DEFAULT_AUTONOMY` already documents as the project-wide starting point ("Autonomy is earned,
not assumed").

This is a **deliberate, temporary, and honestly-labelled stopgap**, not a policy engine in
disguise. The real classifier (blast radius × reversibility × confidence → `auto`/`ask`/
`never`) is the next story on the roadmap and will recompute every action's `policy_class`
from the fields the model *does* provide (`reversible`, `blast_radius`) — this ADR does not
pre-empt that design, it only unblocks the `NOT NULL` constraint until that story exists. The
alternative — asking the model to self-report a policy class — was rejected outright: letting
the reasoning component (read-only, no write access — CLAUDE.md constraint 3) have any say in
its own blast-radius classification defeats the reason that constraint exists.

### Cost is `0`, honestly, not omitted

`Proposal.cost_usd` is `NOT NULL`. Self-hosted local inference has no metered per-token cost,
so it's written as `Decimal("0")` rather than left as a placeholder or a made-up estimate.
`tokens_in`/`tokens_out` are real, summed across every attempt (including the retry) — the
actual compute spent producing this proposal, even though its dollar cost happens to be zero
today. This stops being zero once the Bedrock escalation path (later in Phase 2) can also
produce a `proposal` row.

## Alternatives considered

- **A single validation pass with no retry.** Rejected: it would reject correctable mistakes
  from a small model as readily as genuine failures to diagnose, with no way to tell the two
  apart from the outcome data alone.
- **Coercion instead of rejection** — e.g. clamping an out-of-range confidence to `[0, 1]`
  instead of failing, or filling a missing field with a default. Rejected outright: it's
  exactly the "parses hopefully" behaviour the learn page's Common Mistakes table already
  names as wrong, and a silently-clamped confidence is a miscalibration nobody would ever see.
- **Running the policy engine's real rules inline here**, since the fields needed
  (`reversible`, `blast_radius`) already exist on `DiagnosisAction`. Rejected as scope creep:
  it's a separate story with its own design questions (what counts as `auto`, how confidence
  factors in, whether Jev's risk rating participates) that deserves its own ADR, not one
  folded into this one because the data happens to already be there.
- **Shipping `diagnose.py` as part of the agent's Docker image entrypoint**, running
  automatically after every correlation pass. Rejected for this task: nothing has decided yet
  *when* diagnosis should run relative to correlation, whether it should be a loop like
  `correlate --every`, or how failures should be surfaced without a mobile app to show them
  to. `diagnose.py` ships in the image (the whole `kaval_agent` package is copied wholesale)
  but stays a laptop-invoked tool (`make diagnose INCIDENT=<uuid>`) like `context`/
  `index_runbooks` — unlike `recent_changes.py`, nothing about it actually requires the
  laptop, so wiring it into the deployed loop is a later, purely orchestration, decision.

## Consequences

- `kaval_agent.schema` has no SQLAlchemy or httpx import, so anything that needs the shape —
  a future eval harness, a test, a gateway preview endpoint — can depend on it without pulling
  in the ORM or a live Ollama.
- Every `action` row written by this module today carries `policy_class=ask`. That is
  intentional and matches the project-wide default, but it means the policy engine story's
  "done" bar includes *re-classifying* existing rows, not only classifying new ones, if it
  wants historical proposals to reflect real policy rather than the stopgap.
- `httpx` stays out of the agent's own Docker image (the `agent` extra, laptop-only), so
  `diagnose.py`'s CLI runs from the laptop against the dev server via `make dev-tunnel`, same
  as `context`/`index_runbooks`. See Lab 11.
