# ADR-0017 — The policy engine: OPA/Rego, embedded, and autonomy that has to be earned

- **Status:** Accepted
- **Date:** 2026-09-29
- **Deciders:** Roshan

## Context

Every action KAV-41 writes carries `policy_class=ask`, hardcoded, because the real classifier
didn't exist yet — documented as a deliberate stopgap in `diagnose.py` and called out in
[ADR-0016](0016-json-schema-enforced-proposal-output.md)'s Consequences. `policy/README.md` and
[ADR-0006](0006-jev-as-proposal-risk-rater.md) had already committed to the shape of the real
thing: OPA, written in Rego, evaluated against `blast_radius`, `reversible` and `confidence`,
producing `auto` / `ask` / `never`, "evaluated twice — once by the agent, once by the
executor." This story builds it.

Two questions weren't yet answered by that earlier design:

1. **How does the agent process actually call OPA?** `policy/README.md`'s note and
   `architecture.toml`'s `policy` node (`kind = "service"`) both implicitly assumed something
   long-running and queried over the network, the way `inference` (Ollama) is. But the executor
   that would be the second caller doesn't exist until Phase 3 — today there is exactly one
   caller, inside one process, running in batches (`make diagnose`, laptop-invoked), not serving
   continuous traffic.
2. **What actually stops `auto` from firing on day one?** `policy/README.md` says "Everything
   starts in `ask`. Nothing is born `auto`," and ADR-0006 says promotion "requires pointing at
   rows in the `outcome` table and writing an ADR that cites them." But the stated rule —
   `blast_radius=pod ∧ reversible=true ∧ confidence>0.9` — is just arithmetic over three fields
   the model already produces. Nothing about that rule, as stated, actually *requires* an ADR or
   any accumulated evidence; a sufficiently confident model output would satisfy it on the very
   first real diagnosis, "born auto" in exactly the way the design says must not happen. If
   `outcome` rows don't gate the rule, the sentence "nothing is born auto" is a comment, not a
   property.

There is also an open question `diagnose.py`'s own docstring left for this story: the `action`
rows KAV-41 wrote under the hardcoded `ask` stopgap. Should they be reclassified?

## Decision

### OPA, evaluated by subprocess inside the agent process, not a standalone server

`kaval_agent.policy.classify()` shells out to `opa eval` against `policy/policy.rego`, copied
into the agent's own image alongside a checksum-verified `opa` binary (same pattern the
Dockerfile already uses for `uv`, and the same download-and-verify pattern `ci.yml` uses for
gitleaks and Trivy — just the arm64 build instead of amd64).

This is a narrower design than `architecture.toml`'s original `policy` node implied, and that
file is corrected by this change: `kind` moves from `service` to `library`, and the note now
describes an embedded call, not a networked one. The reasoning:

- **One caller today.** The executor (Phase 3) is the second caller `policy/README.md`
  describes, and it doesn't exist yet. Standing up a queryable OPA server now, for a single
  in-process caller, is a long-running container earning its cost before there's a second
  consumer to justify it.
- **Low, batchy call volume.** This runs once per proposed action, from a laptop-invoked tool,
  not from a request-serving loop. A subprocess call per action is fast enough (single-digit
  milliseconds, measured in Lab 12) that a server's latency advantage doesn't apply here.
- **"One definition, two enforcement points" survives either way.** The property `policy/
  README.md` actually cares about is that the agent and the executor evaluate the *same Rego
  source*, not that they reach it over the same wire. Copying identical `.rego` files into both
  images (the executor's Dockerfile does this too, in Phase 3) gets the same guarantee a shared
  server would, without a new always-on component.
- **Revisit when the executor arrives.** If Phase 3 wants the two callers hitting one running
  evaluator instead — for example so a policy update doesn't need two images rebuilt — running
  `opa run --server` as a real service becomes the better trade, and this ADR's embedded-call
  choice should be revisited then, not assumed to still be right.

If `opa` can't be evaluated at all — binary missing, non-zero exit, output that doesn't
parse — `classify()` returns `PolicyClass.ask`, never `never` and never `auto`. A policy engine
that fails should produce more human questions, not fewer or a false green light: the same shape
as ADR-0006's Jev rules-only fallback, and the same default `.env.example`'s `DEFAULT_AUTONOMY`
already documents.

### `never`: keyword rules over the action type, plus blast_radius=account, never over confidence

`schema.py`'s `DiagnosisAction.type` is free text (there's no enum for it — see ADR-0016), so
`policy.rego` matches case-insensitively against a small keyword set (`pvc`,
`persistentvolumeclaim`, `namespace`, `iam`, `ec2`, `billing`) rather than exact strings, plus a
blanket rule for `blast_radius=account` (the widest radius the schema allows, and a catch-all for
billing/account-wide actions the keyword list might miss). This is deliberately loose in the
safer direction: a false positive costs an extra human question; a false negative costs an
unrefused destructive action. Confidence never enters this decision, per ADR-0006 rule 1.

### `auto`: gated by an explicit, empty-by-default promotion list — "nothing is born auto," made literal

The fix for the gap in §Context: `policy/promotions.json` holds a list, `auto_promotions`, of
promoted `{action_type, blast_radius, requires_reversible, min_confidence, adr}` entries. The
`auto` rule in `policy.rego` only fires for an input that matches an entry in that list — it does
not compute `confidence > 0.9` as a standalone threshold. Today the list is `[]`. That means:

- `classify()` cannot return `auto` for *any* input right now, checked directly (not through a
  mock) in `test_policy.py::test_nothing_is_born_auto` and against the real file on disk — see
  Lab 12 for why the mocked version of this same claim in `policy_test.rego` wasn't enough proof
  on its own.
- Promoting a category to `auto` becomes a one-line data change plus the ADR the existing rules
  already require: add an entry, cite the `outcome` rows that justify it, done. No Rego changes,
  no redeploy logic beyond a normal release.
- The per-promotion `min_confidence` and `requires_reversible` fields mean a future promotion
  doesn't have to reuse the generic `0.9` figure — a category with less evidence, or a wider
  blast radius, can be promoted at a stricter threshold than the first one is.

### A second finding, caught by CI rather than guessed: `__file__`-relative paths don't survive a wheel build

`kaval_agent.policy` originally computed its default `policy/` location by walking four
`.parent`s up from `__file__` — correct for a laptop checkout with an editable install, where
`__file__` still resolves inside the real source tree. It is **not** correct inside the built
agent image: `pyproject.toml`'s hatchling `packages` list installs `kaval_agent` flattened
straight into site-packages, not nested under `services/agent/`, so the same four-parents walk
lands somewhere like `/usr/local/lib/policy` — a directory that doesn't exist.

The failure mode this produced was quiet rather than loud: `opa eval` against a missing
directory exits non-zero, `classify()`'s fallback catches exactly that and returns
`PolicyClass.ask` — so every action would have kept working, silently mis-explained as "the
policy engine is behaving conservatively" rather than "the policy engine can't find its own
policy." CI's per-image check (`docker run ... opa eval`, added specifically because the pure
Python and pure Rego test suites both run from a checkout and couldn't have caught this) failed
loudly instead, which is the entire reason that check exists rather than trusting the two test
suites to cover the deployed shape.

**Fix:** `KAVAL_POLICY_DIR=/app/policy`, set in the Dockerfile, is checked first; the
four-parents walk is now only the fallback for the no-env-var, editable-install case. The same
category of mistake as ADR-0016's `confidence: 70` and this ADR's own mocked-data-path bug
(below): something that is true in every test environment and false in the one environment that
actually matters, found only because a check exercised the real, built thing.

### Existing rows are not reclassified

`kaval_shared.models`'s own module docstring: "Nothing here is ever UPDATEd in place except
`incident.closed_at`... Every other row is written once." `action.policy_class` is one of those
rows. The KAV-41-era stopgap wrote a handful of `action` rows with `policy_class=ask` because
that was, honestly, the whole system's answer at the time. Overwriting them now to reflect what
the policy engine would say today would make the audit trail describe a decision that was never
actually made — the opposite of what an append-only spine is for.

**Decision: leave them as they are.** `write_proposal` calls `policy.classify()` for every action
from this point forward; nothing before this merge is touched. If a historical comparison is
ever wanted — "what would today's policy have said about last week's proposals" — that's a
read-only report computed from the stored fields, not a write to the stored rows.

## Alternatives considered

| Option | Rejected because |
|---|---|
| Run `opa run --server`, queried over HTTP | The second caller (the executor) that would justify it doesn't exist yet; revisit in Phase 3 |
| Compute `auto` from `confidence > 0.9` directly, no promotion list | Satisfies the letter of "auto requires blast_radius/reversible/confidence" but not the stated rule that promotion requires an ADR and outcome evidence — "nothing is born auto" would be a comment, not an enforced property |
| Reclassify existing KAV-41 rows to match the new engine | Violates the append-only invariant `kaval_shared.models` already documents; rewrites what the system actually did |
| An action-type enum instead of keyword matching for `never` | A real, larger change to `schema.py` and every prompt/test that depends on it, for a problem the keyword list already handles in the safer direction; worth reconsidering once real model output shows the keyword list missing something |

## Consequences

**Easier.** A promotion to `auto` is now a reviewable one-line data diff plus an ADR, not a
threshold buried in Rego that changes agent behaviour the moment the first sufficiently
confident proposal happens to appear. The keyword-based `never` rule needs no schema change to
extend — a new dangerous action type is a new list entry.

**Harder.** Two Rego source files and a JSON data file for Roshan to maintain, plus a real `opa`
binary now baked into the agent image and required in CI (`ci.yml`'s `test` job, and the new
per-image check in the `images` job). `policy.py`'s subprocess call is a second thing (besides
Ollama) that can fail at runtime, though it fails toward `ask`, not silently.

**Accepted gap.** Keyword matching on a free-text field can miss a genuinely novel destructive
action type the model invents that doesn't contain any of the six keywords. The intended
backstop is the executor's own second policy check (Phase 3), which — per ADR-0006 rule 3 —
recomputes rather than trusts the agent's fields; this ADR only covers the agent side.

**Revisit if:** the executor (Phase 3) arrives and a shared server would remove real
duplication; the keyword list is shown to miss a real dangerous action type; or the first `auto`
promotion is proposed, at which point *that* ADR is the one that has to cite `outcome` evidence,
not this one.

## Sources

`policy/README.md` and [ADR-0006](0006-jev-as-proposal-risk-rater.md), both already accepted
before this story began. OPA's own documentation on data file loading (`docs.openpolicyagent.io`
— data files merge into `data` at a path derived from the load root and the file's own
top-level keys, not the filename; confirmed directly against `opa eval` while building this, not
assumed — see Lab 12).
