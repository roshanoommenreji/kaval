# Lab 85 — Flattening and capping a signal's text

**Phase:** 8 (hardening) · **Story:** `KAV-113` · **Time:** about 30 minutes · **Cost:** $0 for Parts 1 to 4 (local only, no AWS). Part 5 is the live before-and-after run and costs a few cents of dev-server time.

Before the model diagnoses an incident it is shown the incident's cause, then up to four signal messages. A line break in one could start a line that looks like one
of the message's own headings, and nothing capped how long one could be. [ADR-0072](../adr/0072-a-written-threat-model-and-a-prompt-injection-set.md) found both
(rows T-AGENT-4 and T-AGENT-5) and recorded them as two expected failures. This lab closes them ([ADR-0083](../adr/0083-a-signals-text-is-flattened-and-capped-before-the-model-reads-it.md)).

## Prerequisites

- The repository checked out and `make sync`
- Read [Lab 72](lab-72-the-threat-model-and-the-prompt-injection-set.md) Part 3 if you want the story of the expected failures

## Part 1 — See the gap (on the commit before this change)

```bash
git checkout e3cdeae -- services/agent        # main as it was before KAV-113
python -m pytest services/agent/tests/test_injection.py -q -rx
git checkout HEAD -- services/agent            # put the fix back
```

Two cases show as expected failures (`x`): "a signal message cannot forge a section header" and "one signal cannot make the model input arbitrarily long". That is the gap, written as a test.

## Part 2 — The helper

`kaval_agent.context.flatten_signal_text(text, limit=300)`:

- Every line break, tab, other control character and Unicode line or paragraph separator becomes a space, runs of blanks collapse, and the ends are trimmed.
- If what is left is longer than 300 characters it is cut and ends in "…", so the model can see it was cut.
- Invisible format characters (a zero-width space) are left alone: they cannot start a line.

`query_text_for` calls it for every message it shows, and counts two messages that differ only in blanks as one. The correlator that picks the cause
does **not** call it: it still reads the whole message.

```bash
python -m pytest services/agent/tests/test_signal_text.py -q
```

The tests include the cases that must **not** change: a short plain message comes back exactly as it was, and a message exactly 300 characters long is not marked as cut.
A fix that mangles ordinary text is a worse bug than the gap.

## Part 3 — Why 300

```bash
python -I -c "
import sys; sys.path[:0] = ['services/agent', 'services/shared', '.']
from datetime import UTC, datetime
from evals.golden import GOLDEN_INCIDENTS
at = datetime(2026, 11, 1, tzinfo=UTC)
longest = max(len(str(v)) for c in GOLDEN_INCIDENTS for s in c.build(at)
              for k in ('message', 'reason', 'alertname') if (v := s.value.get(k)))
print('longest golden message:', longest)"
```

The answer is 203, and none contains a line break. So the twenty golden incidents are not changed by the cap, which the before-and-after run in Part 5 confirms.
Kubernetes itself cuts an event message near 1,000 characters, so 300 keeps every ordinary message whole and stops a planted one from filling the input.

## Part 4 — The end-to-end tests and the checks

```bash
python -m pytest services/agent/tests/test_injection.py services/agent/tests/test_signal_text.py -q
ruff check services evals --output-format concise
```

Expect no expected failures at all in the project now (the agent file's two were this story's; the Slack file's six were `KAV-114`'s, [Lab 84](lab-84-escaping-the-text-on-the-slack-card.md)).
`test_a_signal_message_cannot_forge_a_section_header` and `test_one_signal_cannot_make_the_model_input_arbitrarily_long` are ordinary passing tests.

## Part 5 — The before-and-after run (to do, on Roshan's own phrase)

Needs the dev server (`make devbox-up`, `make dev-tunnel`), so it is a live AWS step with its own go. The question is whether the twenty golden numbers move:

```bash
make evals JSON=golden-before.json        # on the commit before this change
make evals JSON=golden-after.json         # on this change
make evals SET=injection                  # the long-padding case now arrives cut to 300 characters
```

Result: **not run yet** (the figures are filled in after the run, never before).

## Checklist

- [x] `flatten_signal_text` with tests that ordinary text is unchanged
- [x] `query_text_for` flattens and caps every message it shows; the correlator still reads the whole message
- [x] The two expected failures are ordinary passing tests
- [ ] The twenty golden numbers measured before and after (the dev-server run)
- [ ] Released, staging smoke passed, promoted (with `KAV-114`)
