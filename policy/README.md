# Policy

What the agent may propose and what the executor may do. Evaluated **twice** — once by the
agent when composing a proposal, once by the executor immediately before acting. The second
check is not redundant: it is what makes a compromised agent harmless.

## Classes

| Class | Criteria | Examples |
|---|---|---|
| `auto` | `blast_radius=pod` ∧ `reversible=true` ∧ `confidence>0.9` ∧ outcome evidence exists | restart a crashlooping pod |
| `ask` | everything not otherwise classified | patch a resource limit, scale a deployment, cordon a node |
| `never` | irreversible, or outside the blast radius, regardless of confidence | delete a PVC or namespace, mutate IAM, terminate EC2, touch billing |

**Everything starts in `ask`.** Nothing is born `auto`.

Promoting a class to `auto` requires pointing at rows in the `outcome` table and writing an
ADR that cites them. "It seemed reliable" is not a promotion criterion.

**How this is enforced, not just stated (KAV-42, ADR-0017):** `auto` is gated by
`promotions.json`, a list of `{action_type, blast_radius, requires_reversible, min_confidence,
adr}` entries that starts `[]`. The Rego rule only fires for an input matching an entry already
in that list — it does not compute a threshold like `confidence > 0.9` on its own. So promoting
a category is a one-line data change plus the ADR this section already requires, and until that
first entry exists, `classify()` cannot return `auto` for any input, checked directly against
this file, not a mock (see [Lab 12](../docs/labs/lab-12-policy-engine.md)).

## Inputs

`blast_radius`, `reversible` and `confidence` are rated by Jev
([ADR-0006](../docs/adr/0006-jev-as-proposal-risk-rater.md)). Those ratings can only make a class
**stricter**:

- `never` is decided from the action type alone, whatever Jev says.
- Nothing Jev returns promotes `ask` to `auto`.
- The executor recomputes blast radius itself rather than trusting the agent's fields.
- If Jev is unavailable, the inputs default conservatively and everything lands in `ask`.

Built in Phase 2 (`KAV-42`, [ADR-0017](../docs/adr/0017-opa-policy-engine-and-earned-autonomy.md)).
Rego, with `opa test` in CI. Evaluated by `kaval_agent.policy` calling `opa eval` inside the
agent process — not yet a standalone server, since the executor (the second caller this section
describes) doesn't exist until Phase 3; both callers will evaluate the identical `.rego` files
regardless of how each one reaches them.
