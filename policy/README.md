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

Built in Phase 2. Rego, with `opa test` in CI.
