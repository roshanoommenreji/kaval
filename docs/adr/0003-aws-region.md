# ADR-0003 — AWS region

- **Status:** Accepted
- **Date:** 2026-08-22 (accepted 2026-09-11, after the Lab 01 Bedrock check)
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

**`ap-south-1` (Mumbai) for all compute, storage, and networking.**

Checked directly in the Bedrock console, Model catalog, region set to Asia Pacific (Mumbai):

- **Claude Sonnet 5** (`anthropic.claude-sonnet-5`) appears in the catalog for this region, but its
  **Inference type is "Cross-region inference," not a direct regional invocation**. This is
  scenario 2 from the three checked above: the model isn't called with a plain single-region model
  ID — Bedrock routes the request through its cross-region inference profile mechanism. Deployment
  type is Serverless; no capacity to provision, no idle cost.
- **One-time account gate, unrelated to region:** Bedrock shows *"Anthropic requires first-time
  customers to submit use case details before invoking a model, once per account."* This has to be
  clicked through once, before Phase 2's first live escalation call — worth doing during Lab 01
  while already on that screen, rather than hitting it as a surprise mid-implementation.

So: **`ap-south-1` stands**, escalation calls go through the cross-region inference profile rather
than a bare model ID. The added latency is a few hundred milliseconds at most on an
already-asynchronous, low-frequency escalation path — acceptable in a way it would not be for the
interactive mobile chat screen, which never touches Bedrock directly.

Model ID and pricing were read directly from the console at decision time rather than recalled;
re-verify with the `claude-api` skill when Phase 2 actually wires the Bedrock client, since IDs and
pricing can change between now and then.

## Consequences

**Easier.** A responsive mobile app, which matters disproportionately because the app is the part
of this project people will actually see.

**Harder.** Bedrock escalation calls go through a cross-region inference profile rather than a bare
model ID — one extra piece of client configuration in the `agent` service, to document when Phase 2
wires it. Some AWS features and pricing promotions reach `ap-south-1` later than US regions.

**Cost.** Broadly neutral. Some services run marginally more expensive in Mumbai than in US
regions; the difference at this scale is cents. Cross-region Bedrock calls would add negligible
data transfer at the volumes involved.

**Revisit if:** Bedrock availability in `ap-south-1` proves so limited that the escalation path
becomes awkward, or if spot reclamation rates in Mumbai turn out to be materially worse than
elsewhere. Moving region later means recreating everything — but since everything is Terraform and
the only persistent state is a Postgres volume with a nightly dump to S3, that is a contained
operation rather than a rebuild.
