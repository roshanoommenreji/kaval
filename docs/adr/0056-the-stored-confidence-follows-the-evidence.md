# ADR-0056 — The stored confidence can never be higher than the evidence allows

- **Status:** Accepted; measured on the dev server and re-checked on staging 2026-10-16 (see "Measured" and "Amendment"); promoted to production's pin (`sha-06b64ba`), live there when production is next brought up
- **Date:** 2026-10-16
- **Deciders:** Roshan
- **Story:** `KAV-88` (Phase 5) · follows [ADR-0018](0018-eval-harness-and-golden-incidents.md) (the two sparse cases that
  falsified "less evidence means less confidence"), [ADR-0054](0054-telling-the-model-which-fixes-exist.md) (confidence rose
  as a side effect) and [ADR-0055](0055-the-model-runs-in-the-cluster-and-diagnoses-new-incidents.md) (proposals now
  appear without a person running anything)

## Context

Every proposal carries a `confidence` between 0 and 1, and a person reads it in Slack next to the approve button. It
was always the model's own number, and the model's own number does not follow how much it was shown:

- Lab 13 (2026-09-30): both "sparse" golden cases (one raw observation, no message) said 0.70 to 0.75, the same as
  the fully evidenced ones. They were left failing on purpose.
- Lab 51 (2026-10-14): after ADR-0054 the mean rose from 0.76 to 0.88 and 60 percent of replies said 0.9 or more;
  the sparse cases now said 0.95.
- Since ADR-0055 the proposal appears on its own, often within a minute of the first signals. A number that is
  confident when the evidence is thin is now what a person sees at 3 a.m. with nobody having looked first.

Where the number is used: shown to the approver (`slack_chatops`), checked by the policy (`auto` needs
`confidence > min_confidence` from `promotions.json`, and that list is empty today), and used by the Bedrock
escalation rule (`kaval_agent.escalate`, not live: `KAV-44`). Nothing acts differently on a *lower* number.

## What real companies do

| Pattern | One line | Fits here? |
|---|---|---|
| Ask the model to rate itself | Put it in the prompt, trust the number | This is what we had. Measured not to work with this model, twice; the prompt already says "keep confidence low if there is no evidence" |
| Sample several times, compare | Run N times, confidence = how often the answers agree (self-consistency) | Sound, but a call is 34 s of the 4 GB node's processor: 3 to 5 minutes and three times the load per incident |
| Calibrate afterwards | Fit a curve from stated confidence to how often it was right (isotonic / Platt) | Needs hundreds of outcomes. We have 20 golden cases and almost no real fixes yet |
| Cap by the evidence | A rule over what the system actually holds, applied outside the model | Free, instant, readable, testable |

**Chosen: the model states a ceiling; our code can only lower it, by a rule over the evidence.** The calibration route
is the right one later, when `outcome` rows have accumulated (see "What this does not do").

## Decision

`kaval_agent.evidence.limit(stated, fingerprint, signals)`, called in `write_proposal` (the one place every proposal is
written, for the laptop, the cluster worker, the evals and the Bedrock path alike). For an incident about a Kubernetes
workload:

| What the incident holds | The stored confidence is at most |
|---|---|
| one observation (or none) | **0.5** |
| several, all of one kind (for example three "restarting" events and never why) | **0.7**, unless that kind is the direct evidence of the incident's own cause (below) |
| several, all of the kind that names the cause (a memory kill for a memory incident; the "exec format error" exit for a wrong-architecture incident) | the model's number, unchanged |
| two or more kinds (a memory reading **and** the kill) | the model's number, unchanged |

Other incidents (a cost spike, an unused disk) are one measurement by nature and have no fix to be overconfident
about; they are not touched. A number the model already put below the cap is never raised. The policy is asked about the
stored number. The model's own number is not discarded: the worker's log line says it, the counter
`kaval_diagnosis_confidence_limited_total` counts each time the rule bites, and the evals print both side by side.

### Amendment: found on staging the first time it ran for real (2026-10-16)

The first version of the rule treated every single-kind incident as thin. On staging, the out-of-memory game day filed
its proposal by itself and the rule bit: the model said 0.95 and **0.70 was stored**. Reading the incident showed why:
a real memory incident holds **one kind** of signal, `pod_oom_killed`, 17 of them and nothing else (the golden cases
build three kinds, a shape the real collector does not produce for this failure, which is why the dev-server run could
not show it). A confirmed kill reported by the kernel is not a hint about the cause, it *is* the cause.

So `DIRECT_EVIDENCE` names, per cause, the one kind whose own report states it (`oom_killed` with `pod_oom_killed`;
`exec_format` with `container_exited`), and several observations of exactly that kind are not limited. One observation
alone is still capped at 0.5 (so the two sparse cases behave as before), and a crash loop, whose restarts say nothing
about why, keeps the 0.7. The lesson recorded for the next rule of this kind: check it against what the real collector
writes on staging before calling it right; a fixture shaped to pass is not a check.

Re-checked on the fixed build `sha-06b64ba`, same game day: 5 `pod_oom_killed` signals, stored confidence **0.80**, no
limit applied; approved fix, limit raised 290 s after the failure began, held; smoke check 7 of 7.

## Security

- It can only **lower** a number, so it can only move a proposal further from `auto`, never nearer. The `never` class
  already ignored confidence (ADR-0006 rule 1) and still does.
- It reads the incident's own signals from the database, with the role the writer already has. No new permission, no
  new network path, nothing mounted.
- A confused or hostile model cannot raise the stored number by wording: the rule never reads its text, only the
  count and kinds of signals, which the collector wrote.

## Failure and rollback

- If the rule is wrong it is wrong in the cautious direction (a lower number). A fix that should have shown 0.9 shows
  0.5 and a person still decides, exactly as today.
- A **timing effect to know about**: the worker diagnoses an incident once, and a few seconds after the correlator opens
  it. An incident diagnosed while it holds one signal keeps 0.5 even if more signals arrive a minute later, because a
  second proposal is not made automatically (ADR-0055). That is the truth about what was known at that moment.
- Rollback is a revert of the one call in `write_proposal`; the old rows are untouched (a proposal's number is never
  rewritten, `proposal` is append-only in spirit and the stored value is a record of what was shown).

## Observability

`kaval_diagnosis_confidence_limited_total` (a count, no labels, no data in it) shows how often the rule bites in each
environment. There is no alert on it: biting is normal behaviour, not a fault. If it ever drops to zero while
incidents with one signal still arrive, the rule has stopped being applied, which a test also pins.

## Cost and capacity

No AWS cost, no new pod, no extra memory (one comparison over a handful of rows). Nothing to measure on the node.

## Testing

Unit (`services/agent/tests/test_evidence.py`): each row of the table; the model's number never raised; a
well-evidenced incident **not** limited (the negative case); non-workload incidents not limited. Real database: the
number stored is the limited one, and a limited number is counted by the worker and not counted when nothing was
limited. Live: `make evals` on the real model (see "Measured").

## People and process

The approver sees a lower number where the system knew less. The Slack message does not yet say *why* (that needs a
column; see below). Runbook: `docs/runbooks/` need no change; the explanation is in this ADR and Lab 53.

## What this does not do

- It does not make the model *better calibrated*. It stops the stored number overstating what the system saw. Whether
  0.9 means "right nine times in ten" is untested: 20 golden cases cannot show it.
- It looks at the **count and kinds of signals**, not whether the runbook matched or whether this failure has been
  seen before. Adding those is a small change once there is a reason to.
- The Slack message shows the number but not the reason. Showing it needs the reason stored (a migration), which was
  deliberately not paid for here: a migration on an old production database is the open `KAV-85`.
- The Bedrock escalation rule still reads the model's own number; that path is not live (`KAV-44`).
- No calibration curve from real outcomes. When `outcome` rows exist in volume, fit one and replace the table.

## Alternatives rejected

- **Telling the model again to be humble.** Already in the prompt; the sparse cases say 0.95.
- **Sampling several times.** Three to five minutes of processor per incident on a node with 872 MB to spare.
- **A new column for the model's raw number and the reason.** Right eventually; costs a migration now.

## Measured

`make evals` on the dev server, the real model (`gemma3:1b-it-qat`), 2026-10-16, one run of the 20 golden incidents,
the current prompt (Lab 53 has the commands):

| | Before (Lab 51, current prompt) | After (this ADR) |
|---|---|---|
| Cases passed | 18 of 20 | **20 of 20** |
| The two sparse cases (stored confidence) | 0.95, 0.95 (failed: must be 0.6 or less) | **0.50, 0.50** (the model still said 0.95) |
| Cases whose stored number was lowered | 0 | **2 of 20 (exactly the two sparse ones)** |
| Mean confidence, said / kept | 0.88 / 0.88 | 0.89 / **0.84** |
| Replies at 0.9 or more, said / kept | 12 / 12 | 12 / **10** |
| Right fix at the right workload | 8 of 8 | 8 of 8 |
| No action where Kaval has none | 6 of 6 | 6 of 6 |
| Actions off the supported list | 0 of 14 | 0 of 14 |
| Never-class action proposed | 0 | 0 (but see below) |

What this says, and what it does not:

* The rule bit where it should and nowhere else: the 18 incidents with two or more kinds of evidence (or not about a
  workload) kept exactly what the model said.
* **The 20 of 20 is partly by construction.** The cap is a rule I wrote knowing what the sparse cases hold, so those two
  passing is a check that the rule is wired in and bites, not evidence that it generalises. The real test is staging
  (Lab 53), where the incidents are not written to fit.
* One run. The cap does not depend on the model (it reads signals), so only the "said" column would vary between runs.
* The eval laptop has no `opa` program, so the policy fell back to `ask` for every action in this run (the printed
  warning). "Never-class: 0" in this run therefore rests on the action *types* being on the supported list (0 of 14
  off it), not on the policy rules. This story does not touch that path; CI runs the policy tests.
