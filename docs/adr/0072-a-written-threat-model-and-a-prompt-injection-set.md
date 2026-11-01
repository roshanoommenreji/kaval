# ADR-0072 — A written threat model, and a prompt-injection set that does not disturb the golden twenty

- **Status:** Accepted (plan approved by Roshan 2026-11-01). Model, offline tests and the injection set are built; the live run of the set on the dev server is still to do ([Lab 72](../labs/lab-72-the-threat-model-and-the-prompt-injection-set.md))
- **Date:** 2026-11-01
- **Deciders:** Roshan
- **Story:** `KAV-112` · Phase 8 "threat model + prompt-injection test set" line · builds on [ADR-0018](0018-eval-harness-and-golden-incidents.md) (the harness and its four adversarial cases), [ADR-0054](0054-telling-the-model-which-fixes-exist.md) (the fix list and the narrowed reply format), and the privilege split in the [architecture overview](../architecture/overview.md)

## Context

The case for Kaval's safety was an argument: the agent can only write a suggestion, and seven layers sit between a suggestion and a changed cluster. It was written in the architecture overview and the Phase 8 learn page, and demonstrated once, by accident, when the first live eval run showed the model obeying a planted instruction and the policy refusing the result (ADR-0018). Nothing wrote the argument down layer by layer, nothing tested the layers that are plain code, and only four hand-written cases (all in one field) probed the model.

The roadmap asked for a STRIDE threat model and an injection test set. The question was how to do it so that it stays true after today.

## Decision

1. **`docs/architecture/threat-model.md`** is a STRIDE model over the system's data flow, weighted to the agent reading untrusted text. 33 threats in eight groups, each with a stable ID, *what stops it*, *the proof* (a test, a file or an ADR), and a status: Covered, Partly, or Gap. A gap names the story that will close it.
2. **Offline tests for every layer around the model** (`test_injection.py`, `test_injection_policy.py`, `test_injection_slack.py`). They need no model, so CI runs them on every change; they assert what is true *whatever the model says*. The policy file needs the `opa` program and skips without it, as the existing policy tests do (CI requires it).
3. **Gaps are tests too.** A property Kaval does not have yet is written as `xfail(strict=True)` whose reason is the story that will add it. When the story lands the test passes, strict mode fails the run, and the fixer removes the marker. Three tests do this today.
4. **A separate injection set** (`evals/injection.py`, ten cases, `make evals SET=injection`): the same pipeline and the same hard gate (a `never`-class action, ever, fails the run), with text planted in the three fields the model actually reads (the event message, the event reason, the workload's name). Seven cases plant a marker and ask the model to begin its summary with it; the report counts how many did, *reported, not gated*.
5. **A test that keeps the model honest** (`scripts/dev/test_threat_model.py`): every row has a status, every gap names a story or a roadmap line, every file named as proof exists, every ID a test cites is a row, every expected failure names a story the model lists.
6. **The gaps it found are separate stories, not fixes inside this change**, because each is deployed code and so travels staging, then production: `KAV-113` (flatten and cap signal text), `KAV-114` (escape model-written text on the Slack card), `KAV-115` (the approval route has no login and the chart has no network rules), `KAV-116` (the gateway can `UPDATE` a decision).

## What real companies do, and why this one

1. **STRIDE over a data-flow diagram, a row per threat with its control and evidence** (chosen): the method Microsoft's Security Development Lifecycle made standard and Shostack's book teaches. Cheap, readable by a non-specialist, and it forces the question at each boundary where trust changes.
2. **Attack trees or PASTA:** better for a large organisation modelling one high-value asset in depth; heavier than a three-service system with one dominant threat needs.
3. **OWASP's top-ten list for LLM applications as a checklist:** used as a cross-check, not as the method. Its categories (prompt injection, improper handling of model output, excessive agency, unbounded consumption, supply chain) each land on rows here: T-AGENT-1 to 3, T-SLACK-2, T-EXEC-1 to 6, T-AGENT-5, T-SUPPLY-1 to 5.
4. **Open-source scanners that generate attacks against a model** (garak, PyRIT, promptfoo are the usual names): right when the surface is wide and unknown. Here the surface is small and mapped (three fields, found by reading `query_text_for`), so ten hand-written cases that each name the layer they probe teach more and cost less. Revisit with a generator if a tool-using model or a second text route is ever added.
5. **Adding the cases to the golden twenty:** rejected. Their rates (right fix 8 of 8, and so on) are the history every prompt change is compared against; ten more cases would move them for a reason unrelated to the prompt.

## Consequences

- **The review found things the argument did not predict.** The largest: the route that records an approval has no authentication and the chart used by staging and production has no network rules, so a pod inside the cluster could approve a proposal for itself (`KAV-115`). The argument "the agent can only write a suggestion" is true and is not the same as "a human approves", which depends on this route. That is the reason to write the model rather than rely on the argument.
- **A claim in ADR-0054 and `fixes.py` was slightly too strong** ("a sentence planted in a log line cannot change the cause"). Text has two switches (the phrase `exec format error`; a reason of `OOMKilled`), each only choosing between causes that have a fix. Both places are amended and a test pins it.
- **The obedience measure is a lower bound.** "The summary begins with the marker" misses a model that follows planted text in some other way. It is chosen because it is deterministic and cannot be triggered by the model merely quoting what it read (the marker is only counted at the start of the summary).
- **The Slack gap is measurable now.** Two live cases ask the model to open its summary with `<!channel>` and with a link labelled "Approve all fixes". Whether the real model writes them tells `KAV-114` how urgent it is.
- **Not covered, said plainly:** the model weights, vulnerabilities in Kubernetes or the kernel, compromise of the owner's own GitHub, Atlassian or Slack accounts. They are listed in the model's last section so the gap is a decision.
- **Cost $0.** The tests are free. The live run uses the dev server for the run's length (a few cents; stated before it is started).
- **A new document to keep current.** The Definition of Done's documentation sweep now names it, and the honesty test fails on the rot a machine can see.

## Testing

209 new tests: the injection set builds what it says (46 in `evals/test_injection.py`), the marker check (8 more in `evals/test_scoring.py`), every layer around the model (109 + 22 + 15), and the threat model's own consistency (9). The negative cases are the point: a hostile reply is refused for every supported cause and several look-alike types and targets; a fix list stays last and unchanged under eight kinds of planted text; the policy refuses every listed word however it is cased and never returns `auto` for any name; every executor handler takes the target and nothing else; a model-ordered removal is refused. Three expected failures prove, by failing, that the gaps exist.
