# ADR-0003 — AWS region

- **Status:** Proposed — pending verification in Lab 01
- **Date:** 2026-08-22
- **Deciders:** Roshan

## Context

The AWS CLI is currently configured for `us-west-2`, which appears to be a leftover default rather
than a considered choice. Region affects four things here:

**Latency to the phone.** The operator console is used from Kerala. Mumbai (`ap-south-1`) is
typically 30–60 ms away; Oregon (`us-west-2`) is closer to 250 ms. For a chat screen and a
websocket feed this is the difference between "instant" and "noticeably laggy" — and this project
gets demonstrated on video, where lag is visible.

**Spot price and capacity.** `t4g.medium` spot pricing differs by region, as does the reclamation
rate. Both need checking against the real console rather than assumed.

**Bedrock model availability.** This is the genuine complication. Bedrock does not offer every
model in every region, and `us-west-2` has historically had the broadest catalogue while
`ap-south-1` has had a narrower one. Cross-region inference profiles exist and may resolve this,
but the answer must be *checked*, not remembered — availability changes frequently and any figure
recalled here would be unreliable.

**Data residency.** Not a real constraint for a personal project with no third-party data. Worth
noting only because the governance framing of this project (ISO 42001, EU AI Act) makes residency
a natural thing for an interviewer to ask about, and "I considered it and here is why it did not
bind" is a better answer than silence.

## Decision

**Proposed:** `ap-south-1` (Mumbai) for all compute, storage, and networking.

For Bedrock escalation, verify in Lab 01 which of these holds and record the answer here:

1. The intended model is available directly in `ap-south-1` → use it, single region, done.
2. It is not, but a cross-region inference profile covers it → use that, and document the
   latency and any data-transfer implication.
3. Neither → call Bedrock in a different region explicitly. Escalation is a low-frequency,
   asynchronous path, so a few hundred milliseconds of additional latency is acceptable there in
   a way it would not be for the interactive chat screen.

Do not resolve this from memory. Check the Bedrock console and use the `claude-api` skill for
current model IDs and pricing.

## Consequences

**Easier.** A responsive mobile app, which matters disproportionately because the app is the part
of this project people will actually see.

**Harder.** Possibly a split-region setup if Bedrock forces it, which means an extra client
configuration and a note in the architecture docs explaining why. Some AWS features and pricing
promotions reach `ap-south-1` later than US regions.

**Cost.** Broadly neutral. Some services run marginally more expensive in Mumbai than in US
regions; the difference at this scale is cents. Cross-region Bedrock calls would add negligible
data transfer at the volumes involved.

**Revisit if:** Bedrock availability in `ap-south-1` proves so limited that the escalation path
becomes awkward, or if spot reclamation rates in Mumbai turn out to be materially worse than
elsewhere. Moving region later means recreating everything — but since everything is Terraform and
the only persistent state is a Postgres volume with a nightly dump to S3, that is a contained
operation rather than a rebuild.
