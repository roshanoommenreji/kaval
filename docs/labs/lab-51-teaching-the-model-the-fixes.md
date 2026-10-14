# Lab 51 — Teaching the model which fixes exist, and measuring it

**Phase:** 5 · **Story:** `KAV-84` · **Time:** about 2 hours with the live runs · **Cost:** the dev server for about two to two and a half hours: about 6 cents at $0.027 an hour (a `t4g.medium` plus its address), plus any surplus-CPU charge while the model ran flat out (up to about 8 cents an hour, not measured; check the CPU-credit line on the next bill). Nothing else in AWS.

The out-of-memory game day found that the local model names the cause and proposes nothing. This lab gives it the
list of fixes Kaval can carry out, and measures the difference with `make evals` before and after. The decision is
[ADR-0054](../adr/0054-telling-the-model-which-fixes-exist.md).

## Prerequisites

- [Lab 13](lab-13-eval-harness.md) done (the eval harness) and the dev server reachable (`make devbox-up`, `make dev`)
- `make dev-tunnel` running in its own terminal

## Step 1 — The unit tests (no database, no model)

```bash
pytest evals/ services/agent/tests/test_fixes.py services/agent/tests/test_narrowing.py -q
```

The ones worth reading:

- `test_the_model_is_told_exactly_the_fixes_the_executor_can_carry_out`: the list the model sees equals
  `kaval_executor.k8s.SUPPORTED_ACTIONS`. Add an executor action without teaching the model and CI is red.
- `test_only_the_fix_that_answers_the_cause_is_shown` and `test_a_cause_with_no_fix_is_told_so_and_shown_no_action_to_copy`.
- `test_diagnose_sends_the_narrowed_schema_for_this_incident` and `test_a_reply_that_breaks_the_rule_gets_one_corrective_retry`.
- `evals/test_baseline.py`: the frozen old prompt really is the old one, and a reply with no `actions` is valid
  for it and invalid today.

## Step 2 — The new measurements

The harness now scores the fix (`evals/golden.py` says what the right one is for each shape):

| Measure | Question |
|---|---|
| Right fix, aimed at the right workload | Of the 8 cases that have a supported fix (4 out-of-memory, 4 exec format), was it proposed, with the incident's own `namespace/name`? |
| Other fixes alongside | Did it also propose fixes that do not fit (a restart for an out-of-memory kill)? |
| No action where Kaval has none | Of the 6 cost-spike and idle-volume cases, did it stay quiet? |
| Types outside the supported list | How many proposed actions are not one of the three? |

Reported, not gated. The gate stays schema validity plus action safety.

## Step 3 — Before and after, in one run

```bash
make evals PROMPT=both JSON=run.json     # also: PROMPT=before, PROMPT=after (default)
```

`before` is the prompt, schema and message exactly as they were (`evals/baseline.py`, frozen); `after` is today's.
Each case builds one context and asks the model twice, so both arms see the same history.

## What actually happened (2026-10-14)

Several attempts. The failures are the lesson.

**Attempt 1 — the list in the system prompt.** Before 0/8, after 0/8, and **no action in any of 40 diagnoses**. The
tokens grew (15,345 to 21,645 in) but the answer did not change. Looking at one raw reply showed the cause:
the replies had no `actions` field at all. `actions` had a default, so it was *optional* in the schema Ollama
decodes against, and a model that is allowed to leave a field out, does.

**Probing one OOM incident, five tries each** (a scratch script, not in git):

| Wording | Result |
|---|---|
| `actions` required, list in the system prompt | a fix in 1 of 5 |
| Same words at the end of the user message | the right type in 5 of 5, but targets invented (`deployment/charts/kaval/values.yaml`, `pod`) and extra fixes |
| System prompt plus a worked example | the example's target (`kaval-demo/checkout`) copied onto other workloads |
| Example at the end, all three fixes listed | right type, right target `kaval-demo/checkout`, but not this workload's name |

**Attempt 2 — list at the end, exact target given, `actions` required.** After: **4/8** (every OOM case right, every
exec-format case empty), 6/6 quiet where Kaval has no fix, 0 of 14 actions off the list. (The "before" arm failed 20 of
20 in this run: it was my harness, which still used the new required `actions` for the old prompt. The baseline now has its own copy of
the old schema model.)

**Attempt 3 — sharpen the rollback wording.** Worse. The model now listed several fixes on nearly every incident,
including `rollback_deployment` for a cost spike, and invented `check_memory_pressure`.

**Attempt 4 — show only the fix that answers the cause.** Still flaky: out-of-memory right in 2 of 6 tries, and
invented `check_memory_usage` and `restart_service`. Prose does not hold a 1B model.

**Attempt 5 — narrow the reply format itself, per incident.** Tried both ways on five fix incidents and a cost
spike, three tries each:

| | Fix proposed | Cost spike |
|---|---|---|
| The model may decline | 3 of 15 | empty 3 of 3 |
| The format requires exactly one action of the right type and target | **15 of 15** | empty 3 of 3 |

So the format is enforced (ADR-0054, Decision 3).

### The final numbers (three full runs, `make evals PROMPT=both`)

| | Before | After |
|---|---|---|
| Right fix, right workload (of 8) | **0, 0, 0** | **8, 8, 8** |
| Other fixes proposed alongside | 0 | 0 |
| No action where Kaval has none (of 6) | 6, 6, 6 | 6, 6, 6 |
| Action types outside the supported list | 0 of 0 proposed | 0 of 14 proposed |
| Never-class actions (the safety gate) | 0 | 0 |
| Schema valid | 20/20 | 20/20 |
| Cases passed | 11, 10, 10 of 20 | 18, 18, 18 of 20 |
| Root-cause keyword match | 47 of 48 | 48 of 48 |
| Prompt tokens per case | about 770 | about 930 (+21 %) |
| Mean stated confidence | 0.76 | 0.88 |
| Replies at 0.9 or more | 10 % | 60 % |

The two cases still failing are the **sparse** ones, in both arms: they expect confidence at or below 0.6 and the
model says 0.75 to 0.95. That is the Lab 13 finding, unchanged, left failing on purpose (ADR-0018). Their confidence
is now 0.95, which is why the confidence line is a side effect to follow up (`KAV-88`).

In the four adversarial cases the injected instructions ("delete the namespace", "terminate the EC2 instance",
"grant AdministratorAccess") were never proposed; each case got a rollback or a restart for its crash loop, class
`ask`.

## What these numbers do and do not say

- The 8 of 8 is largely **by construction**: the format allows only the right fix. What it proves is that the
  cause reaches the fix, the reply format is respected by this Ollama version, and nothing else slipped in.
- It does not measure the model's skill at choosing, because the model no longer chooses. When it was allowed to,
  it declined most of the time.
- The golden incidents are variants of four shapes, so there is no held-out set.
- The two-fix case (a crash loop: rollback or restart) is not covered by a golden case.

## Done when

- [x] `pytest evals services/agent -q` passes (241 passed with the database reachable; 19 skipped, among them the policy tests, which need `opa`, not installed on this laptop)
- [x] `make lint` clean
- [x] `make evals PROMPT=both` run three times on the dev server; the table above is from those runs
- [x] The dev server and the tunnel stopped afterwards
- [ ] Not done: a live proof inside the cluster. Nothing in the cluster calls the model today (`KAV-87`)

## Clean-up

`make devbox-down` (it also stops itself after an hour idle). The eval runs wrote rows to the dev database only,
flagged `synthetic`. The `before` proposals carry the model name with `+baseline-prompt` appended.
