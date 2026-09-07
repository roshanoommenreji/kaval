# Course outline

Working title: **Ship an AI Ops Agent on $14 a Month**

This file grows as the project does. Every lab in `docs/labs/` is a candidate episode; every
ADR is a candidate "why" segment. Do not write this ahead of the work — the value is that it
is a record, not a script.

---

## Why anyone would watch

Most AI-infrastructure content is either a toy on a laptop or an enterprise demo on someone
else's budget. This sits between: a real system, on real AWS, doing real work, for less than
a streaming subscription — where the cost constraint is the pedagogy rather than an apology.

The other differentiator is the guardrail story. Plenty of tutorials hand an LLM a shell.
Almost none show how to bound one, or what evidence it takes before you widen the bounds.

---

## Structure

Roughly one episode per lab, grouped by phase. Each has the same shape:

1. What we are trying to do, and why now
2. The decision, with the alternative that was rejected and the number that decided it
3. The build
4. What broke
5. What it costs

Segment 2 is the differentiator. Segment 4 is what makes it trustworthy.

---

## Episodes

### Part 1 — Foundations
- Why guardrails come before compute
- The $153/month setup nobody warns you about
- k3s vs EKS: what "certified Kubernetes" actually means

### Part 2 — The agent
- Running Gemma on hardware that costs $9/month
- Making an LLM return JSON it cannot wriggle out of
- The privilege split: why the thinking half has no hands
- Evals — how you know the thing is not just confident

### Part 3 — Kubernetes and AWS
- One Helm chart, two clusters
- Spot instances, and building for a machine that can vanish
- GitOps: the cluster rebuilds itself in five minutes

### Part 4 — The phone
- Push notification to approval in one tap
- What an operator actually needs to see at 2am

### Part 5 — Proof
- Breaking production on purpose
- Measuring MTTR honestly
- Earning autonomy: promoting an action from `ask` to `auto`, with the data

### Part 6 — Money
- Teaching an agent to read your bill
- The four mistakes behind every AWS horror story

---

## Raw material

- `docs/labs/` — the reproducible steps
- `docs/adr/` — the arguments
- `docs/journal/` — what actually happened, including the wrong turns
- `docs/cost/actuals/` — real numbers, monthly

The journal is the most valuable of these and the easiest to skip. Wrong turns are the part
viewers cannot get anywhere else.
