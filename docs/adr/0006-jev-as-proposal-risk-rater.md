# ADR-0006 — Jev as the proposal risk rater

- **Status:** Accepted — build scheduled for Phase 2
- **Date:** 2026-09-26
- **Deciders:** Roshan

## Context

On 2026-09-15 TypeSafe AI launched **Jev**, a "System One" model. It is not a chat model. You give
it a situation and a set of questions whose possible answers you define in advance: a `Choice`,
a `Score`, or a yes/no `Noul`. It returns typed answers with calibrated probabilities. It
produces no free text and gives no explanation.

| | |
|---|---|
| Pricing | $0.042 per million input tokens. Output is not billed. |
| Access | Hosted API only (`POST https://api.typesafe.ai/v1/systemone`). Early-access waitlist, keys issued in batches. |
| Free tier | **None documented.** Claims of "$5 free credit" came from SEO sites and are not supported by the launch material. |
| Weights | Proprietary. No paper, and it cannot be self-hosted. |
| SDK | `typesafe-sdk` (Python), `@typesafe-ai/sdk` (JS) |
| Rate limits | 1,200 req/min and 250k tokens/s at launch, which TypeSafe warns may change without notice |

Kaval's Phase 2 agent has to produce a proposal that the policy engine can classify as
`auto` / `ask` / `never`. The inputs to that classification are `blast_radius`, `reversible` and
`confidence` (see [policy/README.md](../../policy/README.md)). Until now the plan was to ask
Gemma 3 1B for them in its JSON output. A 1B model is bad at calibrated numbers: it will happily
emit `confidence: 0.95` for a guess. Schema validation catches `1.5` but not an overconfident
`0.95`.

A typed-decision model with calibrated probabilities matches that part of the problem closely.

## Decision

**Jev rates risk. Gemma still writes the diagnosis.** On each proposal the agent makes one Jev
call asking:

| Question | Type | Maps to |
|---|---|---|
| How far could this action reach? | `Choice`: pod / deployment / namespace / node / cluster / account | `Action.blast_radius` |
| Can this action be undone cleanly? | `Noul` | `Action.reversible` |
| Does the proposed action address the diagnosed cause? | `Noul` | `Proposal.confidence` |

Gemma (with Bedrock as the escalation path) still writes `summary` and `root_cause`. A human
approving from a phone needs the *why*, and Jev cannot provide it.

Every field Jev produces already exists in `services/shared/kaval_shared/models.py`, so the
schema does not change. `Proposal.model` records the versioned model ID Jev reports, and
`tokens_in` / `cost_usd` record the call.

### Three safety rules

Jev is an opaque hosted model and must not become the safety gate.

1. **`never` is decided by rules, not by Jev.** Deleting a PVC or namespace, mutating IAM,
   terminating EC2, or touching billing is refused based on the action type, whatever Jev says
   about blast radius.
2. **Jev can make a class stricter, never looser.** A low Jev confidence or a wide blast radius
   pushes `auto` down to `ask`. Nothing Jev returns can push `ask` up to `auto`. Promotion to
   `auto` still requires an ADR citing `outcome` rows.
3. **The executor does not trust these fields.** Its second policy check recomputes blast radius
   from the action itself, so a wrong or manipulated Jev answer can at worst cause an extra
   question to the human.

Architecture rule #3 (the agent never gets write access) is untouched. Jev is a read-only call,
and the agent's only output is still a `proposal` row.

### Operating rules

- **Pin the version.** Use `JEV_MODEL=jev-1.13.0`, never `jev-latest`. `jev-latest` moves on
  release and can change answers under tuned thresholds. Log the versioned ID from each response.
- **Redact before sending.** The `state` text leaves the cluster, so it goes through the same
  redaction as `execution.stdout` ([ADR-0005](0005-data-durability-and-staging-seeding.md)):
  secrets, account IDs, ARNs and hostnames are replaced with placeholders.
- **Rules-only fallback.** With no key, `JEV_ENABLED=false`, a timeout, or a 429, the fields are
  filled conservatively (`blast_radius` from the action type, `reversible=false`, and a low
  confidence). Every action then lands in `ask`. The system degrades to more human questions and
  never to less oversight.
- **TypeSafe's own API only.** Third-party proxies offering "free Jev keys" (e.g. jev-agent.com)
  must never receive cluster data.
- **Measure it.** The Phase 2 golden-incident evals score Jev's calibration against the
  rules-only baseline. If Jev doesn't beat the rules, it gets switched off.

## Alternatives rejected

| Option | Rejected because |
|---|---|
| Replace Gemma with Jev entirely | No written root cause for the approver. It also gives up the self-hosted diagnosis the project is built around. |
| Gemma produces `confidence` itself | 1B-model self-reported confidence is poorly calibrated, and the `auto` rule turns on `confidence > 0.9`. |
| Bedrock for the risk fields | Roughly 50× the cost per case, and still free text parsed into numbers. |
| Wait until Jev matures | A reasonable choice, but the fallback rule makes early adoption low-risk and the evals will say whether it earns its place. |

## Consequences

**Easier.** Calibrated inputs to the policy engine without asking a 1B model for numbers it can't
produce reliably. Measured cost per decision on the same `cost_usd` field as the other paths.

**Harder.** A second external dependency, and the first one outside AWS. It needs its own secret
(`TYPESAFE_API_KEY`, only in `.env` and later a Kubernetes Secret) and its own failure handling.
Jev's answers can't be explained, so any disagreement has to be examined through the eval set,
not by reading a rationale.

**Cost.** About 200 proposals a day × about 2k input tokens ≈ 12M tokens/month ≈ **$0.50/month**.
It is billed by TypeSafe, **outside the AWS credit and outside the Lambda hard stop**, so the $24
kill switch doesn't cover it. See [budget-plan.md](../cost/budget-plan.md).

**Accepted risk.** A new vendor, 11 days old at decision time, whose pricing TypeSafe itself says
may be subsidised. The fallback rule caps the damage of an outage or a price change at "more
questions to the human".

**Revisit if:** evals show no calibration gain over rules-only, pricing changes materially, the
waitlist hasn't issued a key by the time Phase 2 starts, or an open-weight model of the same kind
appears that could run on the node.

## Sources

Checked 2026-09-26. Several secondary sources disagree on access and free credit; TypeSafe's own
docs at docs.typesafe.ai are authoritative and should be rechecked before building.

- Wikipedia — Jev (AI model)
- DataCamp — "Jev: TypeSafe's System One Model That Never Hallucinates"
- TechCrunch, 2026-09-18 — "A new kind of AI model from a ChatGPT inventor is thrilling developers"
- DEV Community — "How to Use Jev" (rate limits, version pinning)
