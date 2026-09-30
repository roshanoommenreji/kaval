"""Escalates one incident to Amazon Bedrock when the local model can't be trusted with it
(KAV-44, ADR-0019) — the other half of the model-routing design `diagnose.py` only implemented
one branch of: "Gemma 3 1B handles routine incidents matched by pgvector similarity to past
ones. Novel or low-confidence cases escalate to Bedrock Claude Haiku."

**Same two-layer validation as the local path, nothing weaker.** `diagnose.py`'s docstring
explains why constrained decoding alone isn't trusted: it's a property of one Ollama version
and one model, so Pydantic re-validates regardless. Bedrock has no `format` parameter, so the
equivalent here is a *forced* tool call: the model must call `emit_diagnosis`, whose
`input_schema` is `Diagnosis.model_json_schema()` with `strict: true`. The parsed tool input is
then re-validated with the same `Diagnosis.model_validate` regardless — a model that ignores
its own tool schema doesn't get a pass just because it's the expensive one.

**What decides to escalate, and why it isn't just "confidence < threshold".** KAV-43's eval
harness measured this model's confidence directly: both sparse golden cases (minimal evidence)
reported 0.7-0.75, the *same* range as fully-evidenced routine cases — the model's stated
confidence does not reliably track how much it actually had to go on. Trusting that number
alone as the only escalation signal would mean under-escalating exactly the cases most likely to
need it. So `should_escalate` combines three signals, not one:

- the local model failed outright (`diagnose()` raised) — escalating is the only way to still
  answer the incident at all;
- the context builder found nothing to work from (no runbook match, no similar past incident) —
  a structural "this is genuinely novel" signal from retrieval, independent of what the model
  says about itself;
- the model's own stated confidence, kept as a cheap third check even though KAV-43 showed it's
  a weak one today — worth recalibrating once real escalations accumulate outcome data, not
  worth discarding before any of that data exists.

**Cost is real and recorded, never assumed.** `write_proposal` hardcoded `cost_usd=Decimal("0")`
because the local path has none. Bedrock does, and `estimate_cost_usd` computes it from the
response's own token counts against Claude Haiku 4.5's verified Bedrock pricing — read from the
AWS Price List API (`AmazonBedrockFoundationModels`, `ap-south-1`, checked 2026-09-30, not
recalled) rather than the console, matching ADR-0003's own instruction to re-verify pricing at
implementation time rather than trust a figure written months earlier. The cross-region
("Global") tier — $1.00 / 1M input tokens, $5.00 / 1M output tokens — is priced, not the
direct-regional "standard" tier ($1.10 / $5.50), because ADR-0003 already established that Claude
models in `ap-south-1` are invoked through a cross-region inference profile, not a bare regional
model ID. These two numbers will drift; re-verify them the same way before trusting this module
months from now.

**Credentials.** Like `context.py` and `diagnose.py`, this is a laptop-run tool today (ADR-0016:
nothing has decided how the deployed agent gets triggered yet). `AnthropicBedrock` signs
requests with SigV4 via boto3's own default credential chain — the laptop's `kaval` named AWS
profile (Lab 01), never a key written anywhere in this repo. `BEDROCK_MODEL_ID` has no default:
it must be the exact model or cross-region inference profile ID from the Bedrock model catalog
(`aws bedrock list-inference-profiles`), checked live — same reasoning ADR-0003 gives for not
hardcoding one from memory.

**Mantle, tried and rejected — a real finding, not a style choice.** The Anthropic SDK's newer
`AnthropicBedrockMantle` client is the one the `claude-api` skill recommends for new code, so it
was tried first. Live against this account, in `ap-south-1` and in `us-east-1`, for both a
cross-region inference profile and the oldest on-demand Claude 3 Haiku SKU: every call returned
`404 "The model '...' does not exist"` — a clean routing-level rejection, not a credentials or
model-access problem (the same account, same IAM role, same region and model succeed
immediately through the classic `AnthropicBedrock` client, which uses the long-established
`bedrock-runtime` `InvokeModel` path instead of Mantle's newer unified endpoint). Whatever
Mantle needs that this account doesn't yet have — a distinct enablement, a Bedrock API key
instead of SigV4, a narrower regional rollout — the classic client works today without it, so
that's what this module uses. Re-check Mantle again once there's a reason to (it is presumably
the eventual default), rather than fighting a 404 that has an already-working alternative.

    python -m kaval_agent.escalate <incident-id> [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import cast

import anthropic
import httpx
from anthropic.types import MessageParam, ToolChoiceToolParam, ToolParam
from pydantic import ValidationError
from sqlalchemy.orm import Session

from kaval_agent import diagnose as dg
from kaval_agent.context import Context, build_context
from kaval_agent.diagnose import DiagnosisError, ModelUsage
from kaval_agent.schema import Diagnosis

DEFAULT_REGION = "ap-south-1"  # ADR-0003

# Verified 2026-09-30 against the AWS Price List API (offer code
# AmazonBedrockFoundationModels, region ap-south-1), the cross-region ("Global") tier — see the
# module docstring. Re-verify before trusting these; Bedrock pricing is not this module's to
# guess twice.
HAIKU_INPUT_USD_PER_MTOK = Decimal("1.00")
HAIKU_OUTPUT_USD_PER_MTOK = Decimal("5.00")

# A starting point, not a calibrated cutoff — see the module docstring's KAV-43 finding. Revisit
# once real escalations accumulate outcome data to calibrate against.
ESCALATION_CONFIDENCE_THRESHOLD = 0.5

_TOOL_NAME = "emit_diagnosis"


class EscalationError(DiagnosisError):
    """Bedrock failed, or two attempts both returned a tool call that didn't validate. A
    subclass of DiagnosisError so callers that only care "did we get a diagnosis" can catch one
    error type; `diagnose_with_escalation` still distinguishes the two internally."""


def should_escalate(diagnosis: Diagnosis | None, context: Context) -> bool:
    """Whether this incident should go to Bedrock instead of, or in addition to, the local
    model. `diagnosis=None` means the local model call itself failed (see the module
    docstring's three signals)."""
    if diagnosis is None:
        return True
    if not context.runbooks and not context.history:
        return True
    return diagnosis.confidence < ESCALATION_CONFIDENCE_THRESHOLD


def estimate_cost_usd(usage: ModelUsage) -> Decimal:
    """Dollars for one Bedrock call, from its own token counts. Never estimated in advance —
    computed after the fact from what the response actually reports, the same principle
    `diagnose.py` applies to Ollama's `prompt_eval_count`/`eval_count`."""
    tokens_in = Decimal(usage.tokens_in) / Decimal(1_000_000) * HAIKU_INPUT_USD_PER_MTOK
    tokens_out = Decimal(usage.tokens_out) / Decimal(1_000_000) * HAIKU_OUTPUT_USD_PER_MTOK
    return tokens_in + tokens_out


def _tool_schema() -> dict[str, object]:
    return {
        "name": _TOOL_NAME,
        "description": "Return the diagnosis for this incident. Call this exactly once.",
        "input_schema": Diagnosis.model_json_schema(),
        "strict": True,
    }


def escalate(
    context: Context, *,
    client: anthropic.AnthropicBedrock | None = None,
    model_id: str | None = None, region: str | None = None, max_tokens: int = 2048,
    max_attempts: int = 2,
) -> tuple[Diagnosis, ModelUsage]:
    """Ask Claude Haiku 4.5 on Bedrock to diagnose `context`. Pure with respect to the
    database, same contract as `diagnose.diagnose` — writing it is still `write_proposal`'s job.
    `client` is the same test-injection point: an `AnthropicBedrock` built with fake static
    credentials (SigV4 signs against whatever it's given; it only fails, real or fake, at the
    real network call, which a mocked `httpx2.Client` intercepts first) over a mocked
    transport, so this is testable without real AWS credentials or a real Bedrock call."""
    model_id = model_id or os.environ.get("BEDROCK_MODEL_ID", "")
    if not model_id:
        raise EscalationError("no model given and BEDROCK_MODEL_ID is not set")
    region = region or os.environ.get("BEDROCK_REGION", DEFAULT_REGION)

    owns_client = client is None
    bedrock = client or anthropic.AnthropicBedrock(aws_region=region)
    tool = _tool_schema()
    messages: list[dict[str, object]] = [{"role": "user", "content": context.render()}]
    tokens_in = tokens_out = 0
    last_error: ValidationError | str | None = None
    try:
        for _attempt in range(max_attempts):
            try:
                resp = bedrock.messages.create(
                    model=model_id, max_tokens=max_tokens,
                    system=dg.DEFAULT_SYSTEM_PROMPT,
                    # Same shape the SDK's own multi-turn examples use — an assistant turn's
                    # `content` echoes typed response blocks (`resp.content`) straight back as
                    # the next request's message content, verified against the real SDK rather
                    # than assumed; `MessageParam`'s TypedDict is stricter than the dicts this
                    # loop actually builds, so the cast documents that gap instead of hiding it.
                    messages=cast(list[MessageParam], messages),
                    tools=cast("list[ToolParam]", [tool]),
                    tool_choice=cast(
                        ToolChoiceToolParam, {"type": "tool", "name": _TOOL_NAME}
                    ),
                )
            except anthropic.APIError as exc:
                raise EscalationError(
                    f"Bedrock call failed ({model_id} in {region}): {exc}"
                ) from exc
            tokens_in += resp.usage.input_tokens
            tokens_out += resp.usage.output_tokens

            tool_use = next(
                (b for b in resp.content if b.type == "tool_use" and b.name == _TOOL_NAME), None
            )
            if tool_use is None:
                # Forced tool_choice means this should not happen outside a refusal or a
                # max_tokens cutoff — both signal something structural, not a fixable JSON
                # mistake, so this is a hard failure rather than a retry.
                raise EscalationError(
                    f"{model_id} did not call {_TOOL_NAME} "
                    f"(stop_reason={resp.stop_reason!r})"
                )

            try:
                diagnosis = Diagnosis.model_validate(tool_use.input)
            except ValidationError as exc:
                last_error = exc
                messages.append({"role": "assistant", "content": resp.content})
                messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result", "tool_use_id": tool_use.id, "is_error": True,
                        "content": "That did not match the required schema:\n"
                                    f"{exc}\nReply again with corrected arguments.",
                    }],
                })
                continue
            return diagnosis, ModelUsage(tokens_in=tokens_in, tokens_out=tokens_out)
    finally:
        if owns_client:
            bedrock.close()
    raise EscalationError(
        f"{model_id} did not return a valid diagnosis after {max_attempts} attempt(s): "
        f"{last_error}"
    )


@dataclass(frozen=True)
class DiagnosisResult:
    diagnosis: Diagnosis
    usage: ModelUsage
    model: str
    cost_usd: Decimal
    escalated: bool


def diagnose_with_escalation(
    context: Context, *,
    local_model: str | None = None, ollama_base_url: str | None = None,
    local_client: httpx.Client | None = None,
    bedrock_client: anthropic.AnthropicBedrock | None = None,
    bedrock_model_id: str | None = None, bedrock_region: str | None = None,
    max_attempts: int = 2,
) -> DiagnosisResult:
    """Try the local model first, always — it's free and handles most incidents (KAV-43: 20/20
    schema-valid on the golden set). Escalate only when `should_escalate` says so. If Bedrock
    *also* fails: a local diagnosis that exists, even under-confident or evidence-free, beats
    none, so it's still returned — its low confidence or novelty is already visible on the row.
    Only a local failure *and* a Bedrock failure together raise."""
    local_model = local_model or os.environ.get("LOCAL_MODEL", "")
    diagnosis: Diagnosis | None = None
    usage = ModelUsage(tokens_in=0, tokens_out=0)
    local_error: DiagnosisError | None = None
    try:
        diagnosis, usage = dg.diagnose(
            context, base_url=ollama_base_url, model=local_model,
            client=local_client, max_attempts=max_attempts,
        )
    except DiagnosisError as exc:
        local_error = exc

    if not should_escalate(diagnosis, context):
        assert diagnosis is not None  # should_escalate(None, ...) is always True
        return DiagnosisResult(diagnosis, usage, local_model, Decimal("0"), escalated=False)

    try:
        b_diagnosis, b_usage = escalate(
            context, client=bedrock_client, model_id=bedrock_model_id,
            region=bedrock_region, max_attempts=max_attempts,
        )
    except DiagnosisError as exc:
        if diagnosis is not None:
            return DiagnosisResult(diagnosis, usage, local_model, Decimal("0"), escalated=False)
        raise DiagnosisError(
            f"local model failed ({local_error}) and Bedrock escalation also failed ({exc})"
        ) from exc

    model_id = bedrock_model_id or os.environ.get("BEDROCK_MODEL_ID", "")
    return DiagnosisResult(
        b_diagnosis, b_usage, model_id, estimate_cost_usd(b_usage), escalated=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m kaval_agent.escalate",
        description="Force one incident through the Bedrock escalation path directly, "
                     "bypassing the local-model-first decision — for testing the escalation "
                     "call itself. Run from the laptop with `make dev-tunnel` up and "
                     "AWS_PROFILE=kaval set.",
    )
    parser.add_argument("incident_id")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the diagnosis, write nothing to the database")
    args = parser.parse_args(argv)

    from kaval_shared.db import get_engine
    from kaval_shared.models import Incident

    try:
        incident_id = uuid.UUID(args.incident_id)
    except ValueError:
        sys.exit(f"not a valid incident id: {args.incident_id!r}")

    with Session(get_engine()) as session:
        incident = session.get(Incident, incident_id)
        if incident is None:
            sys.exit(f"no incident {incident_id}")
        context = build_context(session, incident, changes=None)

        try:
            diagnosis, usage = escalate(context)
        except DiagnosisError as exc:
            sys.exit(f"escalation failed: {exc}")

        cost = estimate_cost_usd(usage)
        print(diagnosis.model_dump_json(indent=2))
        print(f"\ntokens: {usage.tokens_in} in / {usage.tokens_out} out — ${cost} estimated")
        if args.dry_run:
            print("(--dry-run: nothing written)")
            return 0

        model_id = os.environ.get("BEDROCK_MODEL_ID", "")
        proposal = dg.write_proposal(
            session, incident, diagnosis, usage, model=model_id, cost_usd=cost,
        )
        print(f"\nwrote proposal {proposal.id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
