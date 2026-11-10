# ADR-0083: A signal's text is flattened and capped before the model reads it

**Status:** Accepted on 2026-11-10 (Roshan: "go with your recommendations" on the plan). Built in `KAV-113`; the staging and production proof is still to come (see Consequences).
**Date:** 2026-11-10
**Related:** [ADR-0072](0072-a-written-threat-model-and-a-prompt-injection-set.md) (the threat model that found this gap, rows T-AGENT-4 and T-AGENT-5),
[ADR-0015](0015-context-builder-retrieval-design.md) (the context builder), [ADR-0082](0082-text-the-model-wrote-is-escaped-before-it-goes-into-slack.md) (the same kind of fix on the way out),
[ADR-0081](0081-a-promotion-must-be-a-tagged-release.md) (how this reaches production), [Lab 85](../labs/lab-85-flattening-and-capping-a-signals-text.md), `KAV-113`

## Context

Before the model diagnoses an incident it is shown the incident's cause and workload, then up to four signal messages (the `message`, else the
`reason`, else the `alertname`). That text can be influenced from outside: a pod's own error output becomes an event message.

Two things were wrong, both written down by `KAV-112` as expected-failure tests:

- **T-AGENT-4.** A line break in a message starts a new line in what the model reads, so a message could carry a line that looks like one of the
  message's own headings (`RUNBOOKS:`, `FIXES YOU MAY PROPOSE:`) and pass for a section Kaval wrote.
- **T-AGENT-5.** Nothing capped a message. Kubernetes cuts an event message near 1,000 characters, but a message can reach Kaval by other routes, and
  a very long one fills the model's input and slows the answer (the same text is also sent to the embedding model that finds runbooks).

The damage was already limited: the real list of fixes is always the last thing the model reads, and its answer is checked against that list
afterwards, so a forged section cannot widen what is accepted. This closes the gap rather than relying on that.

## Options (what real companies do)

1. **Flatten and cap where the prompt is built.** Alert and log fields are cut to a fixed length and put on one line before a person's pager or an AI reads them.
2. **Cut it when it is first collected.** Simple, but it throws evidence away and changes what is stored; the correlator also needs the whole message
   (a clue such as `exec format error` can sit far into a long one).
3. **Trust the model not to be fooled.** `KAV-112`'s live runs showed it sometimes is.

## Decision

1. **Option 1, from one helper.** `kaval_agent.context.flatten_signal_text(text, limit=300)` turns every line break, tab, other control character and Unicode
   line or paragraph separator into a space, collapses runs of blanks and trims the ends. If what is left is longer than the limit it is cut and ends in
   "…", so the model can see it was cut. `query_text_for` calls it for every message it shows.
2. **Applied only where the model's text is built.** The stored signal and the correlator that chooses the cause still see the whole message; a test pins that
   a clue at the end of a 5,000-character message still chooses the cause.
3. **300 characters.** Measured: the longest message among the twenty golden incidents is 203 characters and none has a line break, so their scores cannot move
   (the before-and-after run proves it). Four messages at the limit plus the cause phrase stay under about 1,400 characters, where the unfixed input was unbounded.
4. **Counted after flattening.** A message padded with blanks is not cut for the blanks; two messages that differ only in blanks count as one.
5. **Invisible format characters stay** (for example a zero-width space): they cannot start a line, and the injection set keeps the case that uses them.
6. **No new component, permission or alert.** It is a pure function in the agent; a failure of it would show as a changed golden score or a failing test, not
   as an outage, so an alert on the feature itself would have nothing to watch.

## Consequences

**Easier:** the threat-model rows T-AGENT-4 and T-AGENT-5 move from Gap to Covered; the last two expected-failure tests in the agent file are ordinary passing
tests, and none are left in the project; the runbook search gets a bounded, single-line query.

**Harder / limits:**
- A genuine message longer than 300 characters loses its tail in what the model reads (not in what is stored). The cause and the fix list do not depend on it.
- The flattening does not judge the words: `RUNBOOKS:` in the middle of a line still reaches the model as plain text. That is acceptable: it is no longer a heading,
  and the model's answer is still checked. Labelling the text as data is `KAV-117`.
- It reaches production through the usual release round (ADR-0081), together with `KAV-114`: release-prepare, staging smoke, promote, each on Roshan's phrase.
