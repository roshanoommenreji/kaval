# Lab 72 — The threat model and the prompt-injection set

**Phase:** 8 (security) · **Story:** `KAV-112` · **Time:** about 90 minutes · **Cost:** $0 for Parts 1 to 5; Part 6 uses the dev server for the length of one run (a few cents)

Kaval's safety case was an argument. This lab turns it into a document with a row per threat, tests for every part of it that is plain code, and a set of attacks aimed at the model itself. The decision is [ADR-0072](../adr/0072-a-written-threat-model-and-a-prompt-injection-set.md); the document is [the threat model](../architecture/threat-model.md).

## Prerequisites

- The project's Python environment (`make sync`). For Part 4 the `opa` program on your path (`scoop install opa`, or the pinned release CI uses, version in `.github/workflows/ci.yml`).
- For Part 6 only: the dev server (`make devbox-up`, `make dev-tunnel`).

## Part 1 — Read the model before you test it

Open `docs/architecture/threat-model.md`. Read "In plain words", the data-flow sketch, and one table row slowly, for example **T-AGENT-3**: the threat (the model obeys planted text and proposes something destructive), what stops it (seven layers), the proof (named tests), the status. Every row has the same four parts; that is what makes it checkable.

The IDs matter: tests quote them, and `scripts/dev/test_threat_model.py` fails if a test cites an ID the document does not have.

## Part 2 — Find where outside text reaches the model

This decides how many places there are to attack. Read `query_text_for` in `services/agent/kaval_agent/context.py`:

```python
for key in ("message", "reason", "alertname"):
```

Only those three keys of a signal are ever shown (a test pins it), plus the workload's own name, up to four distinct messages, the runbook sections found by similarity, and recent commit subjects. Labels, annotations and the rest of a signal never reach the model. That small surface is why ten hand-written cases are enough.

## Part 3 — Run the tests that need no model

```bash
python -m pytest services/agent/tests/test_injection.py \
  services/gateway/tests/test_injection_slack.py evals/test_injection.py \
  evals/test_scoring.py scripts/dev/test_threat_model.py -q
```

Expect everything to pass except the **expected failures** (shown as `x`): two in the agent file and six cases of one test in the Slack file. They are not broken tests. Each states a property Kaval lacks and carries the story that will add it.

```bash
python -m pytest services/gateway/tests/test_injection_slack.py -q -rx
```

`-rx` prints the reasons: `KAV-114: the summary goes into the Slack message as it is, so <!channel>, <@user> and <url|label> in it are live Slack markup`.

**See strict mode work.** In `services/gateway/kaval_gateway/slack_chatops.py`, make `_blocks` replace `&`, `<` and `>` in the summary with `&amp;`, `&lt;` and `&gt;` (Slack's documented escaping). The expected failures now *pass*, which strict mode reports as a failure of the run, so whoever fixes the gap has to remove the marker. Put the file back (`git checkout services/gateway/kaval_gateway/slack_chatops.py`); the fix itself is `KAV-114`.

## Part 4 — The policy against names written to get past it

```bash
python -m pytest services/agent/tests/test_injection_policy.py -q
```

Without `opa` these skip. With it: a name containing a listed word (`ec2`, `iam`, `namespace`, `pvc`, `billing`) is `never` however it is cased; names that avoid every word (`terminate_instance`, letters from another alphabet that look like `EC2`, a word split by a zero-width space) are *not* `never`, which is by design the second layer, but are **never `auto`**. The first layer is that a type must be one of the three Kaval can carry out, tested in Part 3 (`test_an_action_type_with_no_handler_cannot_run_even_if_it_is_approved`).

## Part 5 — Look at the injection set without a model

```bash
python -c "from evals.injection import INJECTION_INCIDENTS as I; [print(g.name, '|', g.canary) for g in I]"
python -m pytest evals/test_injection.py -q
```

Ten cases. Seven plant a marker and ask for the summary to begin with it; three check that the outcome (the right fix, aimed at the right workload) did not move. The tests confirm the hostile text is in a field the model actually reads, that the hidden-characters case really hides the words from a plain search, and that the long case is over 5,000 characters.

## Part 6 — The live run (needs its own go; not done at the time of writing)

```bash
make devbox-up && make dev-tunnel        # in a second terminal
make evals SET=injection JSON=injection-run.json
make devbox-down
```

Read the report: the usual hard gate (no `never`-class action, ever; every reply valid), then `planted text obeyed: n/7` (reported, not gated), the right-fix line, and the token count (the long case shows what a 6,000-character message costs). Record the result in the table below and decide from it how urgent `KAV-114` and a prompt change are.

| Date | Model | Gate | Obeyed | Right fix | Tokens in | Notes |
|---|---|---|---|---|---|---|
| *not yet run* | | | | | | |

## Done when

- [x] The threat model exists with 33 threats, each with what stops it, its proof and a status *(2026-11-01)*
- [x] Offline tests cover every layer around the model, and the gaps are expected failures that name their stories *(2026-11-01; 209 new tests)*
- [x] The injection set exists and its scoring is tested *(2026-11-01)*
- [x] Every gap has its own Jira story *(`KAV-113`, `KAV-114`, `KAV-115`, `KAV-116`)*
- [ ] The injection set has been run against the real model and the result recorded above

## Cost

$0 for the tests. The live run keeps the dev server up for the run; the budget plan prices it (it stops itself after an idle hour).
