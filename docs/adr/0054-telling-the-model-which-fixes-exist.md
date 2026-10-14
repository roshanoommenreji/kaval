# ADR-0054 — Telling the model which fixes exist: one list, one fix per cause, and a reply format that allows nothing else

- **Status:** Accepted — built and measured (`KAV-84`); the evidence is in [Lab 51](../labs/lab-51-teaching-the-model-the-fixes.md).
- **Date:** 2026-10-14
- **Deciders:** Roshan
- **Story:** `KAV-84` (Phase 5) · follows [ADR-0016](0016-json-schema-enforced-proposal-output.md) (the reply is
  validated), [ADR-0017](0017-opa-policy-engine-and-earned-autonomy.md) (policy decides what may run) and
  [ADR-0018](0018-eval-harness-and-golden-incidents.md) (the eval harness)

## Context

The out-of-memory game day ([post-mortem](../postmortems/2026-10-11-oom.md)) found that the local model
(Gemma 3 1B) named the cause correctly 3 times in 3 and proposed **no action** 3 times in 3. Its prompt never said
which fixes exist. The executor can do exactly three things (`kaval_executor.k8s.SUPPORTED_ACTIONS`):
`increase_memory_limit`, `rollback_deployment` and `restart_pod`.

Two things made the first measurement harder than expected, and both are findings:

1. **The evals could not see actions.** They scored the cause and the safety gate only, so "before and after"
   would have measured nothing. They now score the fix too (Decision 5).
2. **The model could not see the field.** `actions` had a default, which makes it *optional* in the JSON Schema
   Ollama decodes against, and the model left it out of every reply. Telling it about the fixes in the system
   prompt changed nothing: 0 of 8 cases before and 0 of 8 after, and no action in any of 40 diagnoses.

## What real companies do

| Pattern | One line | Fits here? |
|---|---|---|
| Give the model a tool list and let it choose (function calling, an "action menu") | Works with a large model; the list is the whole instruction | Tried. A 1B model proposed all three, none, or an invented fourth, and changed its mind between identical runs |
| Constrain the output itself (grammar, enum, "tool choice" forced) | The model cannot write what the format does not allow | **Chosen** |
| Look the fix up from the cause (a playbook or runbook mapping) and let the model explain | The action is deterministic; the model writes the narrative | **Chosen**, together with the line above |
| Fine-tune on past incidents | Teaches the choice into the weights | Not now: needs a body of labelled outcomes Kaval does not have yet |
| A larger model | Chooses better | Costs more; Bedrock escalation is blocked on the AWS Marketplace payment issue (`KAV-44`) |

## Decision 1 — one list of fixes, in one place, that cannot drift

`kaval_agent/fixes.py` holds the three fixes with what each does, when to use it, when not to, its blast radius,
whether it is reversible, and **which incident causes it answers** (`oom_killed` and `memory_pressure` →
`increase_memory_limit`; `exec_format`, `crashloop` and `container_error` → `rollback_deployment` and
`restart_pod`). Three things read it: the message the model sees, the schema's description of `action.type`
(which used to name `scale_deployment`, a fix the executor has never had), and a test that fails if the list and
`SUPPORTED_ACTIONS` ever differ. Add an executor action without teaching the model, or name one the executor lacks,
and CI is red. Another test fails if a fix names a cause the correlator cannot produce, or a cause that is for a
person (`disk_pressure`, `latency_high`, anything starting `kaval_`).

## Decision 2 — the model is shown the fix for this incident's cause, at the end of the message

The incident's cause is not the model's to guess. The correlator names it from the signals (it is the first part of
the incident's fingerprint), so the lookup is deterministic and a sentence planted in a log line cannot change it.
The message shows only the fixes that answer that cause, or "NO FIX AVAILABLE" when none does, **last** in the
message. Position mattered a great deal: the same words in the system prompt were followed in 1 of 5 tries and
at the end in 5 of 5 (Lab 51). The incident's own `namespace/name` is handed over too, because the model otherwise
invents a target or copies the one from an example.

## Decision 3 — the reply format is narrowed to the incident, and checked again afterwards

For the local model the schema sent to Ollama is narrowed per incident (`fixes.narrow_schema`):

- a cause with a fix: **exactly one action**, whose `type` can only be a fix that answers the cause and whose
  `target` can only be the incident's own workload;
- a cause with no fix (a cost spike, an unattached volume, a full disk, Kaval's own health, anything unclassified):
  **no action is possible**;
- an unknown cause: the schema is left alone.

The parsed reply is checked against the same rules (`fixes.violations`). A reply that breaks them gets the existing
one corrective retry, then fails with the rule named. This is the second layer of ADR-0016's two-layer idea: the
decoder is a property of this Ollama version, so the check is kept.

**What this means, plainly.** When the cause has a supported fix, the proposal contains that fix. The model's
judgement goes into the diagnosis text, the summary, the risk and the confidence; it does not choose the action.
Allowed to decline, the 1B model declined in most tries (Lab 51), so a "propose it unless the evidence says no"
design would have kept the 0 of 8. A person still approves every action, policy still classifies it, and the
executor still re-checks it.

## Decision 4 — `actions` is a required field

Every reply must say what it proposes, even if the answer is an empty list. This alone took the one incident probed
from no action in the full run to a fix in 1 of 5 tries (Lab 51). It also means an older reply with the field missing is now a validation error and
gets the corrective retry.

## Decision 5 — the evals score the fix, and run the old prompt beside the new one

`evals/golden.py` says what the right fix is for each shape (an OOM kill → `increase_memory_limit`; an exec format
error → `rollback_deployment`; a cost spike or an idle volume → no action). `evals/scoring.py` reports the right fix
aimed at the right workload, other fixes proposed alongside, no action where Kaval has none, and action types
outside the supported list. `make evals PROMPT=before|after|both` runs the prompt as it was before this story (kept
frozen in `evals/baseline.py`, with its own copy of the old schema) beside today's, case by case from one context.
Run one after the other on a database that keeps its history, the second run would see different history from the
first. These numbers are **reported, not gated**: the release gate stays schema validity plus action safety
(ADR-0018), until there is a baseline to argue a threshold from.

## Security

- The model still has no write access and still sets no policy class (ADR-0016/0017). Nothing here changes what
  may run; it changes what is suggested.
- The cause comes from the correlator, not from the model, and the target is the incident's own workload (Kubernetes
  names cannot contain spaces or line breaks, so they cannot smuggle text into the message). The executor's
  permissions stay limited to the demo namespace.
- With the format narrowed, the local model **cannot write** "delete the namespace" or "terminate the EC2 instance"
  at all, and in the four adversarial cases it never did. The cost: policy's keyword rules no longer see what the
  model *wanted* on this path. They remain as the second layer, and the executor's re-check as the third.
- A wrong cause means a wrong or no suggestion, which a person sees at approval. A memory leak will still be offered
  a doubling; the runbook and the executor's ceiling already warn against approving it repeatedly.

## Failure and rollback

- If the narrowed reply still fails twice, the diagnosis fails and **nothing is written**, as before.
- A new executor action is not offered to the model until it is added to `fixes.py`; the drift test says so.
- Turning it off is a revert of the one commit: the old prompt and schema are in git and in `evals/baseline.py`.

## Cost and capacity

No AWS cost. The prompt grew from about 770 to about 930 tokens per case (+21 percent); the model's own output
grew from 108 to 113. Nothing runs in the cluster for this story (see What this does not do). Measuring it
took about two to two and a half hours of the dev server: about 6 cents at its $0.027 an hour, plus any
surplus-CPU charge while the model ran flat out (up to about 8 cents an hour; not measured).

## What this does not do

- **Nothing in the cluster calls the model today** *(resolved by `KAV-87`, [ADR-0055](0055-the-model-runs-in-the-cluster-and-diagnoses-new-incidents.md))*. The agent only groups signals; the diagnosis step runs by hand
  or from the evals, and staging's game days file proposals by hand. This story improves what that step suggests.
  Running it for every new incident is its own story: `KAV-87`.
- **The Bedrock escalation path** (`kaval_agent.escalate`) is told the same thing (same message), but its reply is
  not narrowed or re-checked: it cannot run live yet (`KAV-44`), and a rule that cannot be proved is not added.
- **Confidence went up.** The mean stated confidence rose from 0.76 to 0.88, and 60 percent of replies now say 0.9
  or more (10 percent before). Policy uses confidence only to *promote* to `auto`, which is empty, so nothing
  unsafe follows, but the number is less useful. `KAV-88`.
- **A crash loop has two candidate fixes** (a rollback or a restart). No golden case covers it, so the model's pick
  between them is unmeasured.
- **Text planted in a log line** is not stripped from the message. It could not change the action on this path, but
  it can still colour the diagnosis text.
- The 20 golden incidents are variants of four shapes, so there is no held-out set; the prompt wording was tuned on
  the same incidents. The structural result (8 of 8) does not depend on the wording, but it also says little about
  the model's skill: it says the pipeline connects the cause to the fix and the reply respects the format.

## Alternatives rejected

- **The list in the system prompt** (measured 0 of 8, and then 1 of 5 with `actions` required).
- **A worked example in the prompt.** The model copied the example's target (`kaval-demo/checkout`) onto other
  workloads.
- **Showing all three fixes and asking it to choose.** Proposed the other two fixes alongside, a restart for an OOM
  kill (which the runbook says not to do), a rollback for a cost spike, and invented names.
- **Showing only the right fix but letting the model decline.** It proposed it in 3 of 15 tries.
- **A global enum of the three types on `action.type`.** Hides what the model wanted from policy, and still lets it
  list all three.
- **Fine-tuning, or a larger model:** see the table.

## Consequences

- Adding a fix is one entry in `fixes.py` plus the executor handler; the drift test enforces both.
- A cause the correlator names for the first time gets "NO FIX AVAILABLE" until someone decides a fix answers it.
  That is the safe direction.
- The next prompt change can be compared to this one by adding a second frozen copy beside `evals/baseline.py`.
