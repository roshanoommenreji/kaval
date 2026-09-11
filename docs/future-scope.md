# Future scope — beyond v1

Phases 0–9 in [../ROADMAP.md](../ROADMAP.md) are the committed build. This page is the
holding pen for work that is **out of scope for v1** but worth doing afterward, and the
reasoning for why it matters.

The framing question: *does building Kaval make Roshan ready for a Forward Deployed
Engineer role, or the adjacent AI-platform role he is actually aiming at?*

---

## What Kaval already builds

Roughly 70% of the hard-skills bar for both roles:

| Skill | Where Kaval covers it |
|---|---|
| Kubernetes ops, Helm, multi-env promotion | k3s, umbrella chart, `local → staging → prod` with digest pinning |
| Cloud + IaC under real constraints | Terraform modules/envs; the $25/mo ceiling forces the same trade-off reasoning FDEs use daily (Graviton/arm64, no NAT GW, ephemeral EKS) |
| LLM integration | Self-hosted Gemma, Pydantic-validated output, runbook RAG, Bedrock escalation |
| Safe agent design | Read-only `agent` vs. scoped `executor`, human-in-the-loop, evidence-gated autonomy |
| Release engineering | Build-once-promote-artifact, `promote.yml` gate, gitleaks, clean history |
| Written artifacts left behind | ADR / lab / journal discipline as a merge gate |

This is a credible portfolio piece and interview narrative on its own. Stopping after
Phase 6 still yields that.

---

## What a solo greenfield project structurally cannot teach

These are the gaps. None of them are reasons not to build Kaval; they are the reason
Kaval is *necessary but not sufficient*.

1. **No customer.** The core FDE loop is someone else's problem, someone else's
   stakeholders, someone else's definition of done. A solo project can't simulate the
   "walk in with a vague brief" muscle.
2. **No integration hell.** Real FDE work is 50–60% plumbing into systems you don't
   control — legacy APIs, unfamiliar auth, data you can't fully see. Every interface in
   Kaval is one we designed.
3. **Wrong tempo.** Kaval is deliberately slow, gated, and documented. FDE prototyping is
   fast and often disposable, then you harden the 20% that survives. Different reflex.
4. **Narrow stack.** Python + TypeScript only. FDEs often switch stacks per engagement.
5. **Thin frontend, no data engineering, single-tenant, single-node.** No scale,
   multi-tenancy, or performance work anywhere in the plan.

---

## Candidate work to close the gap

Post-v1, not committed. Each is small and deliberately faster / messier than the main build.

### FS-1 — Integration mini-projects (highest value)

Two weekend-sized builds that wire Kaval's agent into a **real third-party system we did
not design**, timeboxed, minimal docs. The point is the plumbing reflex, not the polish.

- Candidate A: ingest a real external billing/metrics API (e.g. a SaaS usage API) as a
  new signal source, mapped into the existing `signal` model.
- Candidate B: push proposals into a real ticketing system (Linear / Jira / PagerDuty)
  instead of only the mobile app, via that system's real API and auth.

Exit: agent acts on data from a system whose API shape we had to reverse-engineer from docs.

### FS-2 — Demo craft

A 5-minute "here is the problem, here is the thing working" walkthrough, recorded and
iterated. FDEs live or die on this. Overlaps with the Phase 9 demo video but is a distinct
skill — practice it as its own thing, against a stopwatch, more than once.

### FS-3 — Stack breadth spike

One small service in a third language (Go for the executor, or a Rust signal parser) to
prove stack mobility. Only if it doesn't compromise the "build once, promote the artifact"
and "environments differ only by values" constraints.

### FS-4 — Client-facing reps

Not code. Use internal "customers" at the current Release Manager job — treat a real team
as a stakeholder, take a fuzzy ask, ship something, demo it back. The only way to build
the dimension items 1–2 above call out.

### FS-5 — Multi-tenant / scale chapter

A dedicated chapter (like the EKS one) that runs Kaval against two simulated clusters with
separate tenancy and a shared control plane. Exercises the parts of platform engineering
Kaval currently skips.

---

## Verdict

For a **pure FDE role**: Kaval covers the technical foundation; FS-1, FS-2, and FS-4 are
what actually make the difference, and none of them are in v1.

For the **AI-platform role Roshan is aiming at**: Kaval is better aligned as-is — safe
agent architecture, model deployment, and eval discipline are the platform-team core.
FS-5 is the most relevant add.
