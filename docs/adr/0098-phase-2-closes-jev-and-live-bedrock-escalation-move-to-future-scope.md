# ADR-0098: Phase 2 closes; the Jev risk rating and the live Bedrock escalation move to future scope

**Status:** Accepted on 2026-11-15 (Roshan: "the pending items in phase 2, can you move it also to future scopes?"). A documentation and tracking change: nothing is built, deployed or changed in AWS.
**Deciders:** Roshan
**Related:** [ADR-0006](0006-jev-as-proposal-risk-rater.md) (Jev as the risk rater), [ADR-0019](0019-bedrock-escalation-and-the-mantle-client-rejection.md) (Bedrock escalation), [ADR-0017](0017-opa-policy-engine-and-earned-autonomy.md) (the policy engine), [ADR-0018](0018-eval-harness-and-golden-incidents.md) (the evals), [ADR-0055](0055-the-model-runs-in-the-cluster-and-diagnoses-new-incidents.md) (the model in the cluster), [ADR-0097](0097-phase-9-is-retired-slack-is-the-approval-surface.md) (the same evening's clean-up of Phase 9)
**Story:** `KAV-151`

## Context

Phase 2 (the agent loop) has been marked "in progress" since September, long after Phases 3 to 7 closed, because of two lines:

1. **The Jev risk rating** (`KAV-27`): a second, typed model that would rate each proposal's blast radius, reversibility and fit, feeding the policy engine. It was never started. It needs an API key from TypeSafe's waitlist (`KAV-28`), which has not arrived.
2. **A live Bedrock escalation** (`KAV-44`): sending a hard incident to Claude Haiku on Bedrock when the local model fails or is unsure. The code is built and unit-tested (`escalate.py`, Lab 14). No live call has ever completed: the AWS account is an Indian-entity (AISPL) one, and AWS restricts the Marketplace subscription that Anthropic models on Bedrock need. Confirmed with AWS Support on 2026-10-01.

Both are blocked by something outside the project, and neither has moved in six weeks. Meanwhile the dashboard shows "current phase: 2" while the real work is in Phase 8, which is a board that does not say what is true.

What runs today without them, and has since Phase 4:

- **The policy engine runs rules-only.** With no rating, the inputs default to the careful side and every action lands in `ask` (or `never`, which is decided from the action type alone). That is ADR-0006's own fallback, and it is how every approval so far was made. Nothing is on the `auto` list.
- **The local model diagnoses alone.** The diagnosis worker in the cluster calls Gemma and files a proposal or nothing. When it cannot say, the incident waits for a person. Golden evals: 20 of 20 valid, measured several times, most recently 2026-11-10.
- **Phase 2's exit gate** ("20 synthetic incidents produce valid, sane proposals; evals pass") has been met since 2026-09-30 and names neither item.

## What real companies do with work blocked outside the team

1. **Keep it open in the phase** and let the phase stay open. Honest about the gap, but the status stops meaning anything.
2. **Move it to the backlog with the blocker written down,** and close the phase on its exit criteria. The usual choice: a milestone closes on what it promised, and blocked extras become backlog items with a named trigger.
3. **Work around the blocker** (another provider, another account). Real work with real cost and a new decision.

## Decision

**Option 2. Both items move to future scope, with what would bring each back, and Phase 2 is marked done.**

1. `ROADMAP.md`: the two lines leave Phase 2's checklist and stay there as struck-through pointers. Phase 2's marker becomes `[x]`.
2. [future-scope.md](../future-scope.md) gains **FS-12 (Jev risk rating)** and **FS-13 (a live escalation to a larger model)**, each with its blocker and its trigger.
3. Jira: `KAV-27`, `KAV-28` and `KAV-44` move from the Phase 2 epic (`KAV-7`) to a new beyond-v1 epic, `KAV-152`. `KAV-44` goes back to To Do. `KAV-7` is closed.
4. **No code is removed.** `escalate.py`, its tests, `make escalate` and Lab 14 stay: they are built, tested and cost nothing while unused. The policy code's rules-only path is the live path and does not change.
5. The architecture file stops tying the Jev and Bedrock boxes to Phase 2, so closing the phase does not turn them into "built". They are labelled future scope and drawn as planned.

### What would bring each back

| Item | Blocker | Trigger to pick it up |
|---|---|---|
| FS-12 Jev risk rating | No API key (TypeSafe waitlist, `KAV-28`) | The key arrives, **and** there is a wish to put something on the `auto` list. Without that wish Jev has nothing to decide: its ratings can only make a class stricter, and everything is already `ask` |
| FS-13 live escalation | AISPL accounts cannot subscribe to Anthropic models on Bedrock | AWS lifts the restriction, or a decision to use another route (a model on Bedrock that needs no Marketplace subscription, or Anthropic's API directly, which would be a new secret and a new egress rule: its own ADR) |

## Planning standard

- **Whole path:** nothing that runs in a cluster changes; no release round (`make version-plan` shows no version moving).
- **Security:** no change. One thing gets simpler to say: the cluster has no path to Bedrock and no TypeSafe key, and now no plan line promises either.
- **Failure:** unchanged and already the designed fallback. No rating means `ask`; no escalation means the incident waits for a person, and an unanswered approval is reminded, escalated and expires ([ADR-0048](0048-approvals-nobody-answers-expiry-and-escalation.md)).
- **Observability:** unchanged. The evals keep reporting "escalation precision" and "Jev calibration" as not measured, now pointing at FS-12 and FS-13.
- **Cost:** $0 today and less on paper. The cost plan carried $1.50 a month for Bedrock and $0.50 for Jev as worst-case lines; neither has ever been spent. They stay in the table marked "parked", and the worst case without them is about $38.75.
- **Testing:** the existing unit tests of `escalate.py` keep running in CI, so the parked code cannot rot unseen.
- **People:** Roshan owns both triggers. FS-13's AWS restriction is checked when the Bedrock model-access page is next opened for any reason; no calendar reminder, because nothing depends on it.

## Accepted, and said plainly

- **Kaval's diagnosis is one small local model with no second opinion.** A novel incident that Gemma cannot explain gets no proposal and waits for a person. That has been true in production all along; this decision stops calling it temporary.
- **"Cost-aware routing" is designed and built but not proven.** The claim that the cheap local model handles most incidents and the expensive one only the hard ones has never been measured, because the expensive path has never run live. It must not be stated as a result in the README, the demo or a resume line.
- **Nothing can earn `auto`.** Without ratings and with an empty promotions list, every action needs a person. For a one-person system that is the safe default, not a gap.

## Rejected

- **Leave Phase 2 open.** The status would stay wrong for as long as two outside parties take.
- **Mark the two lines done.** They are not done.
- **Delete the escalation code.** It is tested, it costs nothing, and FS-13 would only have to rebuild it.
- **Work around the Bedrock restriction now** (Anthropic's API directly, or another model). A new secret in the cluster and a new way out of it, for a path the product has done without for six weeks. If it is wanted, it is FS-13 and gets its own ADR.

## Consequences

- The dashboard's "current phase" moves on from 2. It becomes **4, not 8**: Phases 4, 5 and 6 still carry half-done lines (marked `[~]`: two in Phase 4, four in Phase 5, six in Phase 6), most of them built and proven with one part left, and Phases 4 and 5 are still marked in progress. They were not part of this decision. Reviewing them line by line (done, or to future scope) is the next clean-up before the board can say 8.
- The dashboard's totals lose two open lines, so the percentage rises a little with no work done; like ADR-0097's, that is the plan getting smaller.
- ADR-0006's status changes from "build scheduled for Phase 2" to parked; ADR-0019 says the live proof is parked.
- The Phase 2 learn page keeps saying which two parts were never run.
- Two comments are left as they are, on purpose, because changing them changes an image or the chart: `agent.yaml`'s "until `KAV-44` unblocks it" (still true) and the mentions of Jev in the agent's policy code (they describe the fallback, which is what runs).
