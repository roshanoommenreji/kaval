# Start here

If you are picking this repository up — future Roshan, a course viewer, or a Claude session with
no memory of the last one — read this page first. It should take four minutes.

---

## What Kaval is

An autonomous operations agent with a human in the loop.

It watches a Kubernetes cluster and an AWS bill. When something goes wrong or money is being
wasted, a self-hosted Gemma 3 1B reads the evidence, works out what happened, and writes a
proposed fix. The proposal goes to a phone. A human taps Approve. Only then does anything change.

Everything — every proposal, every human decision, every measured outcome — is recorded
permanently and never edited.

---

## Why it is built this way

Three ideas do most of the work, and they are worth understanding before reading any code.

### 1. The thinking component has no power

The `agent` service holds **read-only** credentials. It cannot restart a pod, scale a node, or
touch AWS. Its only possible output is a row in a database table.

A separate `executor` service holds narrow write permissions and acts only on proposals that have
passed policy and, where required, human approval.

This means a hallucinating or manipulated model cannot damage anything. It can only *suggest*
damage, which a policy engine and a human then reject. If you ever find yourself giving `agent`
write access to make something easier, stop — that is the whole design being undone.

### 2. Autonomy is earned, with evidence

Every action is classified `auto`, `ask`, or `never`.

At the start, **everything is `ask`**. Over time the `outcome` table accumulates real data: this
class of proposal was approved 40 times, executed 40 times, resolved the incident 39 times. That
evidence is what justifies promoting a class to `auto` — and the justification is written down in
an ADR.

The interesting claim this project makes is not "AI fixed my server." It is *"here is how much
evidence it took before I let it."*

### 3. Cost is a first-class constraint

The ceiling is **$25/month**, and the guardrails that enforce it were the first thing built —
before any compute existed. The architecture avoids NAT Gateway ($32/mo), Application Load
Balancer ($18/mo), and a persistent EKS control plane ($73/mo) deliberately, not accidentally.

The project also *reasons about* its own cost. That is the second half of the product.

---

## How to find your way around

| If you want to… | Go to |
|---|---|
| Know where the project stands right now | [../ROADMAP.md](../ROADMAP.md) |
| See what's deliberately left for after v1 | [future-scope.md](future-scope.md) |
| Understand a past decision and why | [adr/](adr/) |
| Understand how a piece works | [architecture/](architecture/) |
| Reproduce a step from zero | [labs/](labs/) |
| Know what a failure means and how to fix it | [runbooks/](runbooks/) |
| See what this actually costs | [cost/budget-plan.md](cost/budget-plan.md) |
| Read what happened on a given day | [journal/](journal/) |
| Turn this into a video or course | [course/outline.md](course/outline.md) |

`runbooks/` is doing double duty on purpose: it is human documentation **and** the corpus the
agent retrieves from when diagnosing. Writing one improves the product.

---

## Before you run anything

Phases 0–3 are entirely local and cost nothing. Do not provision AWS resources until Phase 4, and
not before the budget guardrails from Phase 0 are proven to fire.

```bash
make help    # see what's available
make dev     # local stack — Ollama, Postgres, gateway
```

Toolchain setup: [labs/lab-00-toolchain.md](labs/lab-00-toolchain.md)

---

## The rules that don't bend

1. **The agent never gets write access.** Reasoning is read-only; mutation goes through the executor.
2. **Guardrails before compute.** Nothing that can bill gets created before the thing that stops it billing works.
3. **Images are `linux/arm64`.** The node is Graviton. An amd64 image builds fine and then fails on the cluster.
4. **No secrets in git, ever — including history.** This repo goes public at v1.
5. **A change is not done until it is documented.** See the Definition of Done in [../CLAUDE.md](../CLAUDE.md).
