"""Calls the local model to diagnose one incident, and validates its answer before any of it
touches the database (KAV-41, ADR-0016).

The model only ever gets to suggest field values inside `schema.Diagnosis`. It never sets
`Action.policy_class` — the schema doesn't even have that field. `write_proposal` asks
`kaval_agent.policy` (KAV-42, ADR-0017) to classify each action from the same fields this
module already validated, and OPA's own answer decides `auto` / `ask` / `never`, never the
model. Rows written before KAV-42 landed carry the earlier hardcoded `ask` stopgap and are
left as they are — `action` is append-only (`kaval_shared.models`'s own module docstring),
and rewriting them would erase an honest record of what the system actually did that day.

Two layers keep the output honest, not one:
- Ollama's `format` is given `Diagnosis.model_json_schema()`, so constrained decoding already
  steers the model toward valid JSON with the right field types, enums and bounds.
- The parsed JSON is re-validated with Pydantic regardless. Constrained decoding is a
  property of *this* Ollama version and *this* model; trusting it without a second check
  means that the day either changes, invalid data reaches the database silently. Same
  reasoning `embeddings.py` uses for checking a returned vector's dimension rather than
  trusting the API's contract.

One retry: if the first reply doesn't validate, the validation error is appended to the
conversation and the model gets one more turn to fix it — a 1B model's most common mistake is
small (a string where a bool was asked for). A second failure raises `DiagnosisError` and
writes nothing: "the model returns validated JSON or the attempt fails"
(docs/learn/phase-2-the-agent-loop.md), never half a proposal.

    python -m kaval_agent.diagnose <incident-id> [--dry-run] [--with-changes]
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

import httpx
from kaval_shared.models import Action, BlastRadius, Incident, Proposal, RiskLevel
from pydantic import ValidationError
from sqlalchemy.orm import Session

from kaval_agent import policy
from kaval_agent.context import Context, build_context
from kaval_agent.schema import Diagnosis

DEFAULT_SYSTEM_PROMPT = (
    "You are Kaval, an on-call diagnostic assistant for a Kubernetes cluster and its AWS "
    "bill. You are given context for one incident: retrieved runbook sections, similar past "
    "incidents with their outcomes, and recent code changes. Diagnose the root cause and "
    "propose concrete remediation actions. You cannot run anything yourself — you only "
    "describe what should happen; a separate, policy-gated component decides whether it "
    "actually runs. Respond with JSON matching the given schema and nothing else. If the "
    "context gives no real evidence for a root cause, say so plainly in root_cause and keep "
    "confidence low — do not invent one. confidence is a fraction between 0 and 1 (e.g. "
    "0.8), never a percentage and never a number above 1."
)


class DiagnosisError(RuntimeError):
    """Ollama failed, or two attempts both returned a reply that didn't validate."""


@dataclass(frozen=True)
class ModelUsage:
    tokens_in: int
    tokens_out: int


def diagnose(
    context: Context, *,
    base_url: str | None = None, model: str | None = None, timeout: float = 60.0,
    client: httpx.Client | None = None, max_attempts: int = 2,
) -> tuple[Diagnosis, ModelUsage]:
    """Ask the model to diagnose `context`. Pure with respect to the database — takes a
    already-built `Context`, returns a validated `Diagnosis`; writing it is `write_proposal`'s
    job. `client` is the same test-injection point `embeddings.embed` uses: an `httpx.Client`
    over `httpx.MockTransport`, so this is testable without a real Ollama."""
    base_url = base_url or os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434")
    model = model or os.environ.get("LOCAL_MODEL", "")
    if not model:
        raise DiagnosisError("no model given and LOCAL_MODEL is not set")

    messages: list[dict[str, str]] = [
        {"role": "system", "content": DEFAULT_SYSTEM_PROMPT},
        {"role": "user", "content": context.render()},
    ]
    schema = Diagnosis.model_json_schema()
    owns_client = client is None
    http = client or httpx.Client()
    tokens_in = tokens_out = 0
    last_error: ValidationError | None = None
    try:
        for _attempt in range(max_attempts):
            try:
                resp = http.post(
                    f"{base_url}/api/chat",
                    json={
                        "model": model, "messages": messages, "format": schema,
                        "stream": False, "options": {"temperature": 0.2},
                    },
                    timeout=timeout,
                )
                resp.raise_for_status()
                body = resp.json()
                content = body["message"]["content"]
            except httpx.HTTPError as exc:
                raise DiagnosisError(
                    f"Ollama chat call failed ({model} at {base_url}): {exc}"
                ) from exc
            except (KeyError, TypeError) as exc:
                raise DiagnosisError(
                    f"Ollama's response had no message.content: {exc}"
                ) from exc
            tokens_in += int(body.get("prompt_eval_count", 0))
            tokens_out += int(body.get("eval_count", 0))

            try:
                diagnosis = Diagnosis.model_validate_json(content)
            except ValidationError as exc:
                last_error = exc
                messages.append({"role": "assistant", "content": content})
                messages.append({
                    "role": "user",
                    "content": "That did not match the required schema:\n"
                                f"{exc}\n"
                                "Reply again with corrected JSON only, matching the schema. "
                                "No prose, no markdown fences.",
                })
                continue
            return diagnosis, ModelUsage(tokens_in=tokens_in, tokens_out=tokens_out)
    finally:
        if owns_client:
            http.close()
    raise DiagnosisError(
        f"{model} did not return a valid diagnosis after {max_attempts} attempt(s): {last_error}"
    ) from last_error


def write_proposal(
    session: Session, incident: Incident, diagnosis: Diagnosis, usage: ModelUsage, *, model: str,
) -> Proposal:
    """Persist a validated diagnosis as one `proposal` row plus one `action` row per proposed
    action. Nothing here is called unless `diagnose()` already returned successfully — there
    is deliberately no path that writes an unvalidated diagnosis."""
    proposal = Proposal(
        incident_id=incident.id,
        summary=diagnosis.summary,
        root_cause=diagnosis.root_cause,
        confidence=diagnosis.confidence,
        risk=RiskLevel(diagnosis.risk),
        model=model,
        tokens_in=usage.tokens_in,
        tokens_out=usage.tokens_out,
        # Self-hosted local inference has no metered cost. Stops being 0 once the Bedrock
        # escalation path (later in Phase 2) can produce a proposal instead.
        cost_usd=Decimal("0"),
    )
    session.add(proposal)
    session.flush()  # assigns proposal.id, needed by the actions below
    for a in diagnosis.actions:
        blast_radius = BlastRadius(a.blast_radius)
        session.add(Action(
            proposal_id=proposal.id,
            type=a.type,
            target=a.target,
            params=a.params,
            reversible=a.reversible,
            blast_radius=blast_radius,
            # KAV-42, ADR-0017: policy/policy.rego decides this, not the model — schema.py has
            # no policy_class field for the model to set. The inputs are exactly what this
            # module already validated (blast_radius, reversible from the action; confidence
            # from the proposal as a whole), whoever produced them — Jev (ADR-0006) can supply
            # the same three fields later without this call changing.
            policy_class=policy.classify(a.type, blast_radius, a.reversible, diagnosis.confidence),
        ))
    session.commit()
    return proposal


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m kaval_agent.diagnose",
        description="Diagnose one incident with the local model and write a proposal "
                     "(unless --dry-run). Run from the laptop with `make dev-tunnel` up.",
    )
    parser.add_argument("incident_id")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the diagnosis, write nothing to the database")
    parser.add_argument("--with-changes", action="store_true",
                        help="also look up recent git commits for the incident's workload "
                             "(laptop-only; see kaval_agent.recent_changes)")
    args = parser.parse_args(argv)

    from kaval_shared.db import get_engine  # only a real run needs the database

    try:
        incident_id = uuid.UUID(args.incident_id)
    except ValueError:
        sys.exit(f"not a valid incident id: {args.incident_id!r}")

    with Session(get_engine()) as session:
        incident = session.get(Incident, incident_id)
        if incident is None:
            sys.exit(f"no incident {incident_id}")
        changes = None
        if args.with_changes:
            from kaval_agent import recent_changes as rc
            _cause, _domain, subject = incident.fingerprint.split(":", 2)
            changes = rc.for_workload(subject.rsplit("/", 1)[-1])
        context = build_context(session, incident, changes=changes)

        try:
            diagnosis, usage = diagnose(context)
        except DiagnosisError as exc:
            sys.exit(f"diagnosis failed: {exc}")

        print(diagnosis.model_dump_json(indent=2))
        if args.dry_run:
            print("(--dry-run: nothing written)")
            return 0

        model = os.environ.get("LOCAL_MODEL", "")
        proposal = write_proposal(session, incident, diagnosis, usage, model=model)
        classes = ", ".join(a.policy_class.value for a in proposal.actions) or "no actions"
        print(f"\nwrote proposal {proposal.id} with {len(diagnosis.actions)} action(s): {classes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
