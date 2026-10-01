# Lab 14 — Escalating to Bedrock when the local model can't be trusted

**Phase:** 2 · **Time:** ~30 min · **Cost:** a few cents per real escalation, once the account's
Marketplace payment issue below is cleared — $0 for everything up through Step 3.

`diagnose.py` only ever implemented one branch of the model-routing design: the local Gemma 3 1B
call. This lab builds and exercises the other branch — escalating novel or low-confidence
incidents to Claude Haiku 4.5 on Bedrock — and walks through two real, live findings hit while
wiring it up. The design is [ADR-0019](../adr/0019-bedrock-escalation-and-the-mantle-client-rejection.md).

## Prerequisites

- [Lab 13](lab-13-eval-harness.md) done
- `AWS_PROFILE=kaval` usable from your laptop (Lab 01) — this path is laptop-run, same as
  `diagnose.py` and `context.py`, per ADR-0016
- `make dev` running, and `make dev-tunnel` in its own terminal, if you want to run this against
  a real incident rather than just the unit tests

---

## Step 1 — Read and run the unit tests, no AWS call involved

```bash
pytest services/agent/tests/test_escalate.py -q
```

20 tests, all against a mocked `httpx2` transport — no real AWS credentials or network call is
made. Worth reading two of them closely:

- `test_an_invalid_tool_call_gets_one_corrective_retry_via_tool_result` — the correction protocol
  Anthropic's tool-use actually requires: a `tool_result` block answering the failed call's own
  `tool_use_id`, not a free-text nudge the way `diagnose.py`'s Ollama retry works. Get this wrong
  and the SDK either errors on the next request or silently confuses the model.
- `test_bedrock_failure_falls_back_to_the_low_confidence_local_answer` — if Bedrock also fails,
  a worse local answer still beats losing the incident. Only *both* failing raises.

## Step 2 — should_escalate() on a few cases by hand

```bash
python -c "
from kaval_agent.escalate import should_escalate, ESCALATION_CONFIDENCE_THRESHOLD
print('threshold:', ESCALATION_CONFIDENCE_THRESHOLD)
"
```

Read `should_escalate`'s three signals in `escalate.py`'s module docstring before moving on — in
particular why confidence alone isn't trusted as the only signal, given what KAV-43's eval harness
already measured about this model's calibration.

## Step 3 — try the Mantle client, and watch it fail cleanly

This step is here deliberately, not as a mistake left in the lab by accident. Before writing
`escalate.py`, the newer `AnthropicBedrockMantle` client was tried first — it's what the
`claude-api` skill recommends. Reproduce it:

```bash
python -c "
import anthropic
client = anthropic.AnthropicBedrockMantle(aws_region='ap-south-1')
client.messages.create(model='claude-haiku-4-5', max_tokens=10,
    messages=[{'role': 'user', 'content': 'hi'}])
"
```

Expect `anthropic.NotFoundError: 404 ... "The model 'claude-haiku-4-5' does not exist"` — the same
result for a bare model ID, a cross-region inference profile ID, its full ARN, and even the oldest
on-demand Claude 3 Haiku SKU, in both `ap-south-1` and `us-east-1`. It's account-level, not a
naming mistake. `AnthropicBedrock` (no `Mantle`) reaches the same model immediately with the same
credentials — see ADR-0019 for the full comparison. `escalate.py` uses the classic client.

## Step 4 — the Marketplace payment finding

```bash
python -c "
import anthropic
client = anthropic.AnthropicBedrock(aws_region='ap-south-1')
client.messages.create(model='anthropic.claude-3-haiku-20240307-v1:0', max_tokens=10,
    messages=[{'role': 'user', 'content': 'hi'}])
"
```

At the time this lab was written, this fails with:

```
anthropic.PermissionDeniedError: Error code: 403 - {'message': 'Model access is denied due to
INVALID_PAYMENT_INSTRUMENT: A valid payment instrument must be provided.. Your AWS Marketplace
subscription for this model cannot be completed at this time. If you recently fixed this issue,
try again after 5 minutes.'}
```

This is a genuine account-level AWS Marketplace billing gap — Anthropic's Bedrock models are
delivered as Marketplace listings with their own payment-instrument validation, separate from the
account's general AWS billing that already pays for the dev server.

**Root cause, confirmed 2026-10-01 via AWS Support, not guessed:** this account is billed through
AISPL (AWS's India entity). RBI regulations since March 2022 block AWS Marketplace from accepting
stored card payments for contract-pricing subscriptions on AISPL accounts — which is exactly how
Anthropic's Bedrock models are sold. The console's **Payment Preferences** page confirms it two
ways: "Payment currency: No currency selected," locked from editing by an active e-mandate; and
**AWS Marketplace → Manage subscriptions → Inactive subscriptions** shows the Claude subscriptions
this lab's calls created, both auto-**Terminated** within the same minute they were born. **Fix:**
switch the account's default payment method to **Pay by Invoice** (root user, Payment Preferences
— needs billing contact details, up to 7 days to activate for new Marketplace purchases; a
Marketplace subscription attempt then has one hour to complete payment or it voids). Once that's
done, re-run Step 4; a clean response confirms it, and Steps 5–6 below become runnable for real.

## Step 5 — escalate one real incident (once Step 4 is clean)

```bash
export BEDROCK_MODEL_ID=global.anthropic.claude-haiku-4-5-20251001-v1:0
make signals SCENARIO=oom-crashloop
make correlate
make escalate INCIDENT=<the incident id printed above> DRY_RUN=1
```

Confirm: a valid `Diagnosis` is printed, along with real token counts and a nonzero estimated
cost. Drop `DRY_RUN=1` to write it as a real proposal, `policy`-classified exactly like a local
one.

## Step 6 — let the full decision logic choose

```bash
make diagnose INCIDENT=<a low-confidence or evidence-free incident> ESCALATE=1
```

Without a genuinely low-confidence or evidence-free incident to feed it, this will simply run
local-only and print nothing about Bedrock — that's the correct behaviour, not a broken flag.

## Done when

- [x] `pytest services/agent/tests/test_escalate.py -q` — 20/20 pass, no real AWS call
- [x] `make test` and `make lint` (mypy + ruff, CI's exact scope) both clean
- [x] The Mantle-vs-classic-client finding reproduced (Step 3)
- [x] The Marketplace payment finding reproduced and documented (Step 4)
- [x] Root cause confirmed, not just worked around — AISPL/Marketplace contract-pricing
      restriction, via AWS Support (2026-10-01)
- [ ] **Blocked, left as-is by choice (Pay by Invoice not yet started — see the journal):** a
      real escalation actually written as a proposal (Step 5); `make diagnose ... ESCALATE=1`
      exercised on a real low-confidence case (Step 6)

## What actually happened, live (2026-09-30, root cause confirmed 2026-10-01)

Both findings above are exactly what happened while building this, not a hypothetical someone
might hit — see [ADR-0019](../adr/0019-bedrock-escalation-and-the-mantle-client-rejection.md)'s
Decision section for the full sequence: five Mantle model/ID/region combinations tried and 404'd,
one classic-client call that briefly succeeded before every subsequent call (same model, same
credentials) started returning the Marketplace payment-instrument 403, reproduced again minutes
later rather than accepted as a fluke. The next day, ADR-0019's "Update 2026-10-01" section
records the actual root cause, confirmed through AWS Support rather than guessed further: an
AISPL (India-entity) account restriction on Marketplace contract-pricing subscriptions, dating to
an RBI regulation from March 2022 — not a code bug, not a one-off glitch, and not something more
model-ID guessing would ever have found. UAT is **not** signed off in this story — see the
journal for why an honest "blocked, root-caused" beats a fabricated pass or a model swap made
under time pressure.
