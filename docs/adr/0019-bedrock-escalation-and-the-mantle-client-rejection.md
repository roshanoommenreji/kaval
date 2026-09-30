# ADR-0019 — Bedrock escalation, and why the Mantle client isn't used

- **Status:** Accepted — built, unit-tested, blocked on a live AWS Marketplace payment issue
- **Date:** 2026-09-30
- **Deciders:** Roshan

## Context

`docs/journal/2026-09-30.md`'s own KAV-43 entry named the open thread this closes: "Bedrock
escalation path for low-confidence cases," the second half of the model-routing design
`diagnose.py` only ever implemented one branch of ("Gemma 3 1B handles routine incidents... Novel
or low-confidence cases escalate to Bedrock Claude Haiku"). ADR-0003 already decided the region
(`ap-south-1`) and that Claude models there are reached through a cross-region inference profile,
not a bare model ID — and explicitly deferred the model ID and pricing to be re-checked live "when
Phase 2 actually wires the Bedrock client." This is that wiring.

KAV-43's eval harness also produced a finding this design has to answer to directly: both sparse
golden cases (minimal evidence) reported confidence 0.7–0.75 in two independent live runs — the
same range as fully-evidenced routine cases. The model's stated confidence does not reliably track
how much it actually had to go on. A design that escalates purely on `confidence < threshold`
would under-escalate exactly the cases most likely to need it.

## Decision

### What triggers an escalation

`kaval_agent.escalate.should_escalate` combines three signals, not one:

1. the local model failed outright (`diagnose()` raised) — escalating is the only way to still
   answer the incident at all;
2. the context builder found nothing to work from — no runbook match, no similar past incident
   (`not context.runbooks and not context.history`) — a structural "this is genuinely novel"
   signal from retrieval, independent of what the model says about itself;
3. the model's own stated confidence, against `ESCALATION_CONFIDENCE_THRESHOLD = 0.5` — kept as a
   cheap third check even though KAV-43 showed it's weak today. Discarding it before any real
   calibration data exists would be guessing in the other direction; recalibrate once real
   escalations accumulate outcome data, per the same "measured, not assumed" reasoning ADR-0015
   used for the 0.5 runbook-relevance cutoff.

If Bedrock's own call also fails and the local model produced *something* (even under-confident or
evidence-free), that local answer is still returned rather than losing the incident — its low
confidence or novelty is already visible on the row. Only a local failure *and* a Bedrock failure
together raise.

### Same two-layer validation as the local path

`diagnose.py`'s two-layer contract (constrained decoding, then Pydantic re-validation regardless)
carries over rather than being weakened for the "better" model. Bedrock has no `format` parameter
equivalent to Ollama's, so the analogous mechanism is a *forced* tool call: the model must call
`emit_diagnosis`, whose `input_schema` is `Diagnosis.model_json_schema()` with `strict: true`. The
parsed tool input is re-validated with the same `Diagnosis.model_validate` regardless — a model
that ignores its own tool schema doesn't get a pass just because it's the expensive one. One
corrective retry, same as the local path, but via the protocol Anthropic's tool-use actually
requires: a `tool_result` block answering the same `tool_use_id`, not a free-text nudge — verified
against the real SDK, not assumed (see Verification).

### Cost is computed from the response, never assumed

`write_proposal` hardcoded `cost_usd=Decimal("0")` because the local path has none. It now takes a
`cost_usd` parameter; `escalate.estimate_cost_usd` computes Bedrock's from the response's own
token counts against pricing checked live (below), the same principle `diagnose.py` already
applies to Ollama's own reported token counts rather than trusting an estimate made in advance.

### Pricing, checked live rather than recalled — 2026-09-30

Read from the AWS Price List API (`AmazonBedrockFoundationModels`, `ap-south-1`), not the console
and not memory:

| Tier | Input $/1M tokens | Output $/1M tokens |
|---|---|---|
| Direct-regional ("standard") | $1.10 | $5.50 |
| Cross-region ("Global") | **$1.00** | **$5.00** |

The cross-region ("Global") tier is priced, matching ADR-0003's own established routing — and,
usefully, it matches Anthropic's first-party API price for Claude Haiku 4.5 exactly, which is a
cheap sanity check that the right SKU was found. `HAIKU_INPUT_USD_PER_MTOK` /
`HAIKU_OUTPUT_USD_PER_MTOK` in `escalate.py` carry today's date in a comment so a future reader
knows to re-verify rather than trust them. At the volumes this path will see — an occasional
escalation, not routine traffic — this comes in well under `docs/cost/budget-plan.md`'s existing
$1.50/month "Bedrock escalations (light use)" line, which was already budgeted before this story
and needed no revision.

### The Mantle client was tried first, and rejected — a live finding, not a style choice

The `claude-api` skill's own guidance is to prefer `AnthropicBedrockMantle` — the SDK's newer,
unified Messages-API-shaped Bedrock endpoint — over the older `AnthropicBedrock` client. It was
tried first, live, against the real `kaval-dev` account (which already holds broad
`AdministratorAccess`, so this was never an IAM permissions question):

- `global.anthropic.claude-haiku-4-5-20251001-v1:0` (the bare cross-region inference profile ID),
  its full ARN, the bare first-party model slug `claude-haiku-4-5`, and even the oldest on-demand
  `anthropic.claude-3-haiku-20240307-v1:0` SKU — every one of them, in both `ap-south-1` and
  `us-east-1` — returned `404 "The model '...' does not exist"` from Mantle's endpoint.
- The exact same account, region, IAM role and model, called through the classic `AnthropicBedrock`
  client (the long-established `bedrock-runtime` `InvokeModel` path, still SigV4 via the same
  boto3 default credential chain) reached the model immediately — no 404, no IAM error.

Whatever Mantle needs that this account doesn't yet have — a distinct enablement, a Bedrock API
key instead of SigV4 (its own auth resolver checks for exactly that: `AWS_BEARER_TOKEN_BEDROCK` /
`ANTHROPIC_AWS_API_KEY` env vars before falling back to SigV4), a narrower regional rollout — the
classic client works today without it. `escalate.py` uses `AnthropicBedrock`, documented as a
deliberate, evidence-backed choice rather than an oversight, with a note to re-try Mantle once
there's a reason to (it is presumably the eventual default surface).

### A second live finding, further along: AWS Marketplace, not code

Past the Mantle rejection, the classic client's *first* live call — plain Claude 3 Haiku, no
escalation logic involved — succeeded outright. The very next call, and every one after it
including the intended Claude Haiku 4.5 Global profile, failed instead with:

> `403 PermissionDeniedError: Model access is denied due to INVALID_PAYMENT_INSTRUMENT: A valid
> payment instrument must be provided.. Your AWS Marketplace subscription for this model cannot
> be completed at this time.`

This is an AWS Marketplace billing-configuration state on the account, not a code defect, not an
IAM gap and not a model-ID mistake — confirmed by the fact that the identical call, same model,
same client, same credentials, succeeded once and then started failing with an error that names
its own cause explicitly. Anthropic's models on Bedrock are delivered as AWS Marketplace listings
(`"Claude Haiku 4.5 (Amazon Bedrock Edition)"`, confirmed against the Price List API's own
`servicename` field), and Marketplace subscriptions carry their own payment-instrument validation
separate from the account's general AWS billing. This needs a console action — checking AWS
Billing → Payment methods and/or re-completing the Marketplace subscription for these models — not
a further guess at model IDs or client configuration, which is exactly why this ADR stops
narrowing here rather than trying a sixth variant.

## Consequences

**Built, tested, not yet live-verified.** Every unit test runs against a mocked transport and
passes; the module's request/response handling was independently smoke-tested against the real
SDK's actual shapes for both `AnthropicBedrockMantle` and `AnthropicBedrock` before either test
suite was written — so the code is not "written and hoped," but the end-to-end live call that
would prove a real escalation through this exact pipeline still can't complete until the
Marketplace payment issue clears. UAT is **not** signed off in this story; see the journal.

**Easier.** Nothing about the design changes once the payment issue clears — `BEDROCK_MODEL_ID`
is already a plain environment variable, `AnthropicBedrock` is already the working client, and the
smoke tests already proved the exact request/response shapes this account and region actually
return.

**Harder.** One more place ("which Bedrock client class") where this account's real behavior
diverges from generic SDK guidance, now documented so it isn't re-discovered the hard way in
Phase 7 (FinOps) or wherever Bedrock is next touched.

**Revisit if:** the payment issue clears — the escalate CLI and `make evals`-adjacent
verification below become runnable, and Mantle is worth trying again once there's a specific
reason to (a feature only it supports, or confirmation the account has whatever it's currently
missing).

## Verification

- `.venv/Scripts/python -m pytest services/agent/tests/test_escalate.py -q` — 20 tests, all pure
  against a mocked `httpx2` transport, no real AWS call.
- `make test` and `make lint` (ruff + mypy, the exact CI scope: `services/ evals/
  scripts/dev/check_commits.py scripts/tracking/jira_adf.py`) both clean.
- The tool-use retry protocol (a `tool_result` block answering the failed call's `tool_use_id`,
  not free text) was verified against the real installed `anthropic` 1.9.0 SDK with a scratch
  script before any test was written for it — the SDK accepted and correctly re-parsed the
  correction, confirming the pattern rather than assuming it from the skill's plain-text example.
- Live, against the real `kaval-dev` account: five distinct Mantle model/region/ID combinations
  all 404'd; the classic `AnthropicBedrock` client succeeded once, then started failing with the
  Marketplace payment-instrument 403 documented above — reproduced on a second attempt minutes
  later, ruling out a transient blip (AWS's own error text suggests retrying after 5 minutes;
  it did not resolve).
- **Not yet run, pending the Marketplace fix:** `make escalate INCIDENT=<uuid>` against a real
  incident on the dev server; `make diagnose INCIDENT=<uuid> ESCALATE=1` on a genuinely
  low-confidence or evidence-free case.
