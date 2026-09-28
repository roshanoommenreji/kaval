# Lab 11 — Schema-enforced proposal output: the model actually gets called

**Phase:** 2 · **Time:** ~30 min · **Cost:** about 1–2 cents of dev-server time

The context builder ([Lab 10](lab-10-context-builder.md)) assembles what a person on call
would read; this lab sends it to the local model and validates what comes back before any of
it becomes a `proposal` row. The design is
[ADR-0016](../adr/0016-json-schema-enforced-proposal-output.md).

## Prerequisites

- [Lab 10](lab-10-context-builder.md) done: runbooks indexed, `make context` works
- `make dev` running, and `make dev-tunnel` in its own terminal
- The chat model pulled: `docker exec kaval-ollama-1 ollama pull gemma3:1b-it-qat` (or let it
  happen the first time `make dev` runs — `model-pull` already does this as part of the
  stack)

---

## Step 1 — Diagnose an incident, without writing anything

```bash
make signals SCENARIO=oom-crashloop
make correlate
make diagnose INCIDENT=<the incident's id> DRY_RUN=1
```

```json
{
  "summary": "Container checkout exceeded its memory limit (256Mi) and was killed. Back-off
  restarting failed container checkout. KubePodCrashLooping.",
  "root_cause": "Insufficient memory allocated to the container. The container was
  attempting to exceed its memory limit, leading to a kill and subsequent restart failure.",
  "confidence": 0.8,
  "risk": "medium",
  "actions": []
}
(--dry-run: nothing written)
```

A plausible, grounded root cause — it names the actual limit from the signal payload, not a
guess. `--dry-run` means nothing was written; check `SELECT count(*) FROM proposal` before and
after to confirm.

## Step 2 — For real

Drop `DRY_RUN=1`:

```bash
make diagnose INCIDENT=<the same incident id>
```

```
wrote proposal <uuid> with 0 action(s), policy_class=ask
```

Zero actions here is a legitimate answer, not a failure — the schema allows an empty
`actions` list, and a 1B model correctly declining to propose a specific remediation for a
config-level problem (raise the memory limit, but by how much?) is more honest than inventing
a number. Confirm in Postgres:

```sql
SELECT model, tokens_in, tokens_out, cost_usd, risk, confidence FROM proposal ORDER BY created_at DESC LIMIT 1;
```

`cost_usd` is `0` — self-hosted inference has no metered cost (see ADR-0016) — and
`tokens_in`/`tokens_out` are real, non-zero.

## Step 3 — A workload with real context behind it

```bash
make signals SCENARIO=exec-format
make correlate
make diagnose INCIDENT=<the new incident id>
```

`make diagnose` always passes `--with-changes` (unlike `context`, which makes it opt-in —
see the Makefile). `gateway` is a real service, so the context includes actual recent commits
([Lab 10](lab-10-context-builder.md), Step 4) and the diagnosis reasons about them by name.

## Step 4 — Confirm the actions, when there are any

Any incident whose diagnosis includes `actions` writes one `action` row per action, every one
with `policy_class = 'ask'`:

```sql
SELECT type, target, reversible, blast_radius, policy_class FROM action WHERE proposal_id = '<uuid>';
```

There is no policy engine yet — that's the next roadmap story — so `ask` is the honest,
temporary answer for every action, matching `.env.example`'s `DEFAULT_AUTONOMY`.

---

## Done when

- [x] `make diagnose INCIDENT=<uuid> DRY_RUN=1` prints a plausible diagnosis and writes nothing
- [x] `make diagnose INCIDENT=<uuid>` writes exactly one `proposal` row, `cost_usd = 0`,
      `tokens_in`/`tokens_out` non-zero
- [x] Every `action` row it writes has `policy_class = ask`
- [x] A real service's incident (`gateway`) produces a diagnosis that references its actual
      recent commits
- [x] KAV-41 signed off: `jira-sync.py uat KAV-41 pass --env dev`

## What went wrong, and why (2026-09-28)

- **A 1B model returned `confidence: 70` — twice, including after the corrective retry
  quoted the exact validation error back at it.** Ollama's structured-output `format` (the
  Pydantic-generated JSON Schema) constrains *shape* — field names, types, enum membership —
  but not the numeric `minimum`/`maximum` bounds also present in that same schema; constrained
  decoding is grammar-based and a bounded-float grammar isn't one of the guarantees it makes.
  This is exactly the gap [ADR-0016](../adr/0016-json-schema-enforced-proposal-output.md)
  argued for re-validating with Pydantic instead of trusting `format` alone — found live, on
  the very first incident that happened to trigger it, not hypothetically. Two `proposal` rows
  did **not** get written for this incident while the bug was live — the fail-closed path held.
  **Fixed** by making the system prompt state the format explicitly ("a fraction between 0 and
  1, e.g. 0.8, never a percentage and never a number above 1") — after which the same incident
  diagnosed cleanly on the first attempt, confidence `0.95`. The retry mechanism didn't fix it
  by itself: telling the model *why* a reply was wrong turned out to be less reliable, for this
  model size, than telling it the right format up front.
- **`.venv` locally needed `pip install -e ".[agent]"` before `kaval_agent.diagnose` (which
  imports `httpx`) would import** — `.venv` only had the core dependency set installed from an
  earlier session. A one-time fix; `make sync`/`uv sync --all-extras` covers it going forward.
