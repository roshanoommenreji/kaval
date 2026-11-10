# ADR-0082: Text the model wrote is escaped before it goes into Slack

**Status:** Accepted on 2026-11-09 (Roshan: "go with your recommendations" on the plan). Built in `KAV-114`; the staging and production proof is still to come (see Consequences).
**Date:** 2026-11-09
**Related:** [ADR-0072](0072-a-written-threat-model-and-a-prompt-injection-set.md) (the threat model that found this gap, row T-SLACK-2),
[ADR-0026](0026-slack-chatops-and-deferred-mobile.md) (the Slack connection), [ADR-0048](0048-approvals-nobody-answers-expiry-and-escalation.md) (the reminder messages),
[ADR-0081](0081-a-promotion-must-be-a-tagged-release.md) (how this reaches production), [Lab 84](../labs/lab-84-escaping-the-text-on-the-slack-card.md), `KAV-114`

## Context

The approval card a person reads before pressing Approve shows a summary the model wrote. The model reads text an outsider can influence
(an event message, a runbook, a change note). Slack treats `<`, `>` and `&` as control characters in a message: `<!channel>` pings everyone,
`<https://...|Approve all fixes>` draws a link under any label, `<@U...>` mentions a person.
The summary, the action type and the target went into the message exactly as written, so a model that obeyed planted text could put a live ping
or a misleading link on the card. The model did not do this in either live run of the injection set, so it was not being exploited, but nothing
stopped it. `KAV-112` recorded the gap as six expected-failure test cases; this story removes them.

The same text reaches Slack in more places than the card: the plain line Slack shows in a notification, the "still waiting", "nobody answered" and
"expired" messages, the "what will happen" lines under a removal card, and the service name in a health alert.

## Options (what real companies do)

1. **Escape the three characters** (`&amp;`, `&lt;`, `&gt;`). Slack's own documented fix. Slack shows the characters as plain text, so nothing is lost.
2. **Put model text in plain-text blocks.** Safest, but the card would have to be redesigned and the bold type line split from the summary.
3. **Strip the characters.** Simple, but it loses meaning ("free memory < 10%").

## Decision

1. **Escape, from one helper.** `services/gateway/kaval_gateway/slack_text.py` has `escape()` (the three characters, ampersand first so it is never
   escaped twice) and `code()` (the same, plus backticks turned into apostrophes, for text that sits between backticks, because one would close the
   code span early). Every place that puts a summary, an action type, a target, a "what will happen" line or a service name into a Slack message
   goes through it. Messages Kaval writes itself, such as the `<!here>` in an escalation, do not: that one is meant.
2. **A public card builder for the eval.** `slack_chatops.card_text()` and `fallback_text()` are public so the injection set checks the real card,
   not a copy of its logic.
3. **The injection set reports it.** `make evals SET=injection` builds the real card from every summary the model wrote and prints
   "approval cards with live Slack markup: n/7 (gated: must be 0)" (7, because only the seven cases that plant a marker get a card verdict; corrected 2026-11-10, it first said n/10). It is gated, unlike "planted text obeyed", because it is the gateway's code and
   not the model's behaviour: the right number is zero whatever the model writes. The golden twenty are untouched.
4. **No new component, permission or alert.** The agent still has no write access, the buttons still carry the action's own id, and the existing
   alert on a failed Slack post already covers the path.

## Consequences

**Easier:** the threat-model row T-SLACK-2 moves from Gap to Covered; the six expected failures are now ordinary passing tests; a future change that
adds a new Slack message with model text has one helper to call and a test shape to copy.

**Harder / limits:**
- Formatting characters other than the three (`*`, `_`, `~`) in a summary still format, which Slack's documentation does not treat as a control risk.
  They cannot ping or link.
- A raw `&amp;` the model really wrote shows as `&amp;`, not `&`. Rare, and it is the faithful rendering of what the model wrote.
- Staging has no Slack, so the proof of the card is the injection set run against the real pipeline, which now reports the card result. That run was made on the dev server (0/7 cards with live markup, 2026-11-10), not on staging; the staging smoke test checks the build, not the text. The first real
  card seen in Slack is production's. Production started on 2026-11-10 (23:32 IST) running this build; the gateway's
  Slack notifier connected and ran its 30-second loop with no errors, but no card appeared because production had
  no proposal waiting for approval. The first real card still waits for one.
- It went through the usual release round (ADR-0081) on 2026-11-10, each step on Roshan's phrase: release-prepare (#361, `v0.3.0`, build `sha-5ec8e6a`), staging smoke (7 of 7), promotion (#364).
