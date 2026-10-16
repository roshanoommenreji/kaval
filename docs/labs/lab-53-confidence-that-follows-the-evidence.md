# Lab 53 — A confidence number that cannot be higher than the evidence

**Phase:** 5 · **Story:** `KAV-88` · **Time:** about 2 hours, most of it waiting for the model · **Cost:** the dev server (about $0.027 an hour); staging while checking (about $0.045 an hour); nothing recurring.

Until this lab the confidence on a proposal was whatever the model said, and it said 0.75 to 0.95 whether it had one
raw reading or five. This lab limits the stored number by what the incident actually holds. The decision is
[ADR-0056](../adr/0056-the-stored-confidence-follows-the-evidence.md).

## Prerequisites

- [Lab 52](lab-52-the-model-in-the-cluster-and-the-diagnosis-worker.md) done (proposals are written by a worker)
- For Steps 3 and 4: the dev server up (`make dev`) and `make dev-tunnel` running in its own terminal
- `opa` on the PATH if you want the policy checks to be real (the laptop here has none; see Step 4)

## Step 1 — The rule, on its own

```bash
pytest services/agent/tests/test_evidence.py -q       # 10 tests; the last two need the database
```

Read these first, they are the rules:

- `test_a_single_observation_caps_a_confident_model_at_half` and `test_several_observations_of_one_kind_cap_at_seven_tenths`:
  the two limits (0.5 and 0.7).
- `test_two_kinds_of_evidence_are_left_alone`: the negative case. A well-evidenced incident keeps the model's number.
- `test_a_number_below_the_cap_is_never_raised`: it only ever lowers.
- `test_incidents_that_are_not_about_a_workload_are_not_capped`: a cost spike or an unused disk is one measurement by nature.
- `test_write_proposal_stores_the_limited_number`: it is applied in the one place every proposal is written.

## Step 2 — The worker counts it

```bash
pytest services/agent/tests/test_diagnoser.py -q -k limited
```

`kaval_diagnosis_confidence_limited_total` goes up each time the stored number is lower than the model's, and the worker's
log line says `confidence 0.50 (the model said 0.80; thin evidence)`. The same test file checks that a number that was
not limited is not counted.

## Step 3 — The 20 golden incidents on the real model

```bash
make dev && make dev-tunnel                  # the tunnel in its own terminal
make evals JSON=evals-kav88.json             # about 20 minutes; the run prints the report at the end
```

On a laptop where `python` is not the project's, put the project's environment first on the path
(`export PATH="$PWD/.venv/Scripts:$PATH"`) or `make` fails with `No module named 'kaval_agent'` after pulling the models.

Result (2026-10-16): **20/20 passed**. Both sparse cases stored 0.50 (the model had said 0.95); no other case was
lowered; the new report line reads `confidence: the model said 0.89 on average, kept 0.84; 0.9 or more: said 12, kept 10;
lowered by the evidence: 2`. Right fix 8 of 8, no action where none exists 6 of 6, 0 of 14 actions off the supported
list. The full table is in the ADR's "Measured".

## Step 4 — Be honest about what this run did and did not prove

- Passing the two sparse cases is by construction: the rule is written to cap exactly such incidents. It proves the
  rule is wired in and bites, not that it is right in general.
- With no `opa` installed the policy printed `could not run 'opa' ... classifying ... as ask` for every action. The
  `never` gate in the eval then rests on action *types*, not on the rules. CI runs `opa test`.
- The cap is applied to the number that is **stored**. Slack shows the stored number but not the reason; that needs a
  column and a migration (see the ADR), left for later.

## Step 5 — Staging, then production

Done through the normal route, with nothing special for this story: merge, the release builds the image, staging runs it,
`make staging-smoke` records the pass, `promote.yml` carries the same bytes to production. What to look for on staging:
an out-of-memory game day (`make chaos-run EXPERIMENT=oom`) produces a proposal whose number is whatever the model said if
the incident held a memory reading, the kill and the restarts (2+ kinds), and `kaval_diagnosis_confidence_limited_total`
stays at 0 for it; an incident diagnosed while it holds a single signal is limited to 0.5.

### What staging showed (2026-10-16, build `sha-6603033`)

`make chaos-run EXPERIMENT=oom` (run in the background by mistake, so nobody approved in the 15 minutes staging allows; the
runner timed out, deleted the victim and switched itself off, nothing was left broken). The worker had already diagnosed
the incident by itself, about 100 s after the failure began, and its log line read
`confidence 0.70 (the model said 0.95; thin evidence)`. A read-only job on staging listed the incident's signals:
**17 of `pod_oom_killed`, no other kind**. So the first rule limited a well-evidenced incident, the exact thing its
negative test was meant to prevent, and only the real collector's shape could show it. Fixed (see the ADR's
amendment), merged separately, re-checked on a new build below.

## Done when

- [x] `pytest services/agent/tests evals -q` passes with the database up
- [x] `make lint` clean on the changed files
- [x] `make evals` on the real model: both sparse cases at 0.5, nothing else lowered (2026-10-16)
- [x] Staging check: found the rule too blunt for a real memory incident (stored 0.70, model 0.95; the real incident holds 17 kill events and no other kind); fixed with `DIRECT_EVIDENCE` (ADR-0056, "Amendment"); re-check of the fixed build below
- [ ] Production gets it through the gate

## Clean-up

`make devbox-down`.
