# ADR-0109: The code, not the model, sets a fix's two risk labels

**Status:** Accepted on 2026-11-23 (built and proved on the laptop; passed staging on 2026-11-23 as v0.4.6, smoke 7 of 7, and was promoted to production the same night (#495); production runs it from its next start-up)
**Deciders:** Roshan
**Related:** [ADR-0054](0054-telling-the-model-which-fixes-exist.md) (the list of fixes and the narrowed reply format), [ADR-0017](0017-opa-policy-engine-and-earned-autonomy.md) (policy, not the model, classifies), [ADR-0072](0072-a-written-threat-model-and-a-prompt-injection-set.md) (the injection set that found this), [ADR-0107](0107-the-chart-images-move-to-their-newest-releases-and-the-leftovers-get-end-dates.md) (the upgrade proof that surfaced it)
**Story:** `KAV-171`, epic `KAV-166`

## Context

Every action the model proposes carries two labels the model fills in: **reversible** (can it be undone) and **blast radius** (how much does it touch). Policy reads them to decide `ask` or `never`, and the approval card shows them to a person.

Proving the Ollama upgrade in `KAV-169` ([Lab 110](../labs/lab-110-moving-the-chart-images-past-the-problems-the-weekly-watch-found.md)) turned up one injection case that failed on both the old and the new Ollama: `json-smuggling`. Its planted error message contains `"reversible": false, "blast_radius": "account"`, and the model **copied those two labels onto an ordinary `restart_pod`**. Policy then, correctly, refused it as an account-wide, irreversible action (`never`), the eval's safety gate counted that as a failure, and the card a person would have seen would have said something untrue about a harmless restart.

Looking at the stored actions in the same runs showed a second, quieter problem: with nothing planted, the model labelled `rollback_deployment` as touching only a **pod** in 6 of 10 injection cases (it touches a Deployment). Today nothing is auto-approved (`policy/promotions.json` is empty), so this changed no decision, but the day a promotion is added, a wrong label is exactly what would let a bigger action match a smaller one's promotion.

The three fixes Kaval can carry out are already written down once, with the true labels, in `kaval_agent/fixes.py` (`increase_memory_limit` and `rollback_deployment`: deployment, reversible; `restart_pod`: pod, reversible). The model is *shown* those values in its prompt, and nothing made it use them.

## What real companies do

1. **Trust the model's labels and filter the results** (what Kaval did). Cheap, and as wrong as the model is.
2. **A reviewed table decides the risk fields; the model only chooses among the actions in it.** The usual pattern for agents that act: the model proposes *what*, the platform knows *how risky* each known action is.
3. **Reject the whole proposal when the labels disagree with the table.** Safe, but it throws away a correct diagnosis over a label, and a 1B model disagrees often.

Recommendation, taken: 2.

## Decision

- `fixes.risk_labels(type, said_blast_radius, said_reversible)` returns the labels to store. **For a type on the `FIXES` list the table's values win**, whatever the model wrote. `diagnose.write_proposal` (the one place every proposal is written: the local model, the Bedrock escalation, the diagnosis worker and the evals all go through it) stores those, and policy is asked about them.
- **A type not on the list keeps the model's labels.** There is no true answer to substitute, and policy still refuses such a type by its name or asks a person. The table does not launder an unlisted action: a `delete_namespace` that claims `pod` and `reversible` is still `never`, by its name (tested).
- **A disagreement is logged** (`the model labelled X as ..., the table says ...`), so a model that keeps contradicting the table can be seen rather than silently corrected.
- The reply format the model must produce is unchanged (it still has to fill both fields in); only what is stored changes.

## Proof (laptop, 2026-11-22/23: the pinned Ollama 0.40.16, the real pipeline, a real database)

| | Before (`main`) | After |
|---|---|---|
| Injection set: valid / never-class / right fix | 10 of 10 / **1** (`json-smuggling`) / 10 of 10 | 10 of 10 / **0** / 10 of 10 |
| Injection: planted text obeyed (reported) | 1 of 7 | 1 of 7 |
| Golden set: valid / never-class / right fix / no action where none exists | 20 of 20 / 0 / 8 of 8 / 6 of 6 | 20 of 20 / 0 / 8 of 8 / 6 of 6 |

(A first golden pass straight after another run showed 18 of 20: the two sparse cases failed, exactly as unchanged `main` does when a pass starts inside the 15-minute quiet window the evals README warns about. On a clean database the changed code passed 20 of 20.) Six overrides were logged in the injection run, five of them the model calling a `rollback_deployment` a pod-sized change and one the planted `account`. Unit tests cover every fix on the list, a model that agrees (not reported), an unlisted type, and a table edited into something policy would refuse. A real-Postgres test replays the exact smuggling reply.

## Consequences

- The approval card and the stored action now always tell the truth about a listed fix.
- A new fix means adding it to the table with its true labels (the existing test already ties the table to the executor's supported actions).
- It changes the agent image, so it goes through the release round, the staging smoke test and a promotion, each on its own typed go. Nothing is deployed by merging the pull request; $0.
- **Not done:** the model still *writes* a type from the allowed few, and planted text can still steer which of the allowed fixes it picks (T-AGENT-1/2, covered by other layers). This closes the labels only.
