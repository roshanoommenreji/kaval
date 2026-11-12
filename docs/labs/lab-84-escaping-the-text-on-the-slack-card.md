# Lab 84 — Escaping the text on the Slack card

**Phase:** 8 (hardening) · **Story:** `KAV-114` · **Time:** about 30 minutes · **Cost:** $0 for Parts 1 to 4 (local only, no Slack, no AWS). Part 5 is the live proof and costs about 1 cent for a staging smoke test.

The approval card shows a summary the model wrote. Slack reads `<`, `>` and `&` as control characters, so a summary containing `<!channel>` pings
everyone and `<https://...|Approve all fixes>` draws a link under any label. [ADR-0072](../adr/0072-a-written-threat-model-and-a-prompt-injection-set.md)
found the gap (row T-SLACK-2) and recorded it as six expected failures. This lab closes it ([ADR-0082](../adr/0082-text-the-model-wrote-is-escaped-before-it-goes-into-slack.md)).

## Prerequisites

- The repository checked out and `make sync`
- Read [Lab 72](lab-72-the-threat-model-and-the-prompt-injection-set.md) Part 3 if you want the story of the expected failures

## Part 1 — See the gap (on the commit before this change)

```bash
git checkout b6c2624 -- services/gateway      # main as it was before KAV-114
python -m pytest services/gateway/tests/test_injection_slack.py -q -rx
git checkout HEAD -- services/gateway          # put the fix back
```

Six cases show as expected failures (`x`) with the reason "the summary goes into the Slack message as it is". That is the gap, written as a test.

## Part 2 — The helper

`services/gateway/kaval_gateway/slack_text.py` has two functions:

- `escape(text)` replaces `&` with `&amp;` first, then `<` with `&lt;` and `>` with `&gt;`. Slack shows these as the plain characters. Order matters:
  doing the ampersand last would escape the ampersands the other two just wrote.
- `code(text)` does the same and turns backticks into apostrophes, for text between backticks (the action type and target), because a backtick inside
  would close the code span and let the rest be formatted.

```bash
python -m pytest services/gateway/tests/test_slack_text.py -q
```

The tests include the cases that must **not** change: `restart checkout`, `memory at 91% (limit 1Gi)`, `kaval-demo/checkout`. A fix that mangles ordinary
text is a worse bug than the gap.

## Part 3 — Where it is used

Every place that puts model-written or outsider-influenced text into a Slack message:

| Message | Code |
|---|---|
| The card: summary, type, target | `slack_chatops._blocks` |
| The "what will happen" and "to undo" lines under a removal card | `slack_chatops._preview` |
| The plain line Slack shows in a notification | `slack_chatops.fallback_text` |
| Still waiting, nobody answered, expired | `approvals._what` |
| The service name in a health alert | `health_alerts._headline` |

The `<!here>` in the escalation message is **not** escaped: Kaval wrote it and it is meant to ping.

```bash
python -m pytest services/gateway -q
```

Expect no expected failures from the Slack file any more (the two in the agent file belonged to `KAV-113`, which removed them on 2026-11-10, [Lab 85](lab-85-flattening-and-capping-a-signals-text.md); none are left).

## Part 4 — The injection set reports the card

`make evals SET=injection` now builds the real card from every summary the model wrote (`evals/card.py`) and prints one more line:

```
approval cards with live Slack markup (real card code, KAV-114): 0/7 (gated: must be 0)
```

The denominator is 7, not 10: only the seven cases that plant a marker get a card verdict (this lab first said 10; corrected 2026-11-10). It is gated, unlike "planted text obeyed": the card is the gateway's code, so the right number is zero whatever the model writes. The unit tests behind it:

```bash
python -m pytest evals/test_scoring.py -q
```

They prove the detector can say yes (`has_markup("<!channel>")` is true), so the zero means something, and that the golden twenty get no card verdict.

## Part 5 — Result so far and what is left

- Built and tested locally on 9 November 2026: the whole `services` and `evals` suites pass, `ruff` is clean, and `mypy` reports only an error that was
  already there (a test that needs `scripts/ops` on the path, which the project's own lint run provides).
- **Released 2026-11-10, each step on Roshan's own phrase:** release round (#361, `v0.3.0`, build `sha-5ec8e6a`), `make staging-smoke` (7 of 7), promotion (#364).
  The injection set was run on the dev server (0/7 cards with live markup), not repeated on staging.
- **First real card seen:** seen by Roshan in Slack on 2026-11-13 (four cards, all alike; production's cost detector filed four real old-snapshot proposals at 23:45-23:46 IST on 2026-11-12 and the notifier posted each): the italic *Summary:* label before the model's sentence, the plain-words *What will happen* and *To undo* lines, the *Nobody has approved this yet. Approving or denying is your decision.* closing line, and the Approve and Deny buttons. The cards were left unanswered on purpose (production was parked). Staging has no Slack, so the card in a real channel was always production's.

## Checklist

- [x] `slack_text.escape` and `code`, with tests that ordinary text is unchanged
- [x] The card, its notification line, the reminder messages, the removal preview and the health alert use them
- [x] The six expected failures are ordinary passing tests
- [x] The injection set prints and gates "cards with live Slack markup"
- [x] Released, staging smoke passed, promoted (`v0.3.0`, 2026-11-10)
- [x] Production ran the build (2026-11-10): the Slack notifier connected, its loop ran with no errors
- [x] First real card seen in Slack *(2026-11-13: four real cards from production's own proposals, see above)*
