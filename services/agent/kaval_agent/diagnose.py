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

This module is only the local half of the model-routing design. `kaval_agent.escalate`
(KAV-44, ADR-0019) is the other half: novel or low-confidence incidents escalate to Bedrock
Claude Haiku instead, through the same two-layer validation contract. `--escalate` on this
module's CLI opts into that path; without it, this always was and still is local-model-only.

    python -m kaval_agent.diagnose <incident-id> [--dry-run] [--with-changes] [--escalate]
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
from kaval_shared import health
from kaval_shared.models import Action, BlastRadius, Incident, Proposal, RiskLevel
from pydantic import ValidationError
from sqlalchemy.orm import Session

from kaval_agent import evidence, fixes, policy
from kaval_agent.context import Context, build_context
from kaval_agent.schema import Diagnosis

BASE_SYSTEM_PROMPT = (
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

# KAV-84, ADR-0054: the model is told which fixes exist. Before this it was not, and it named the
# cause and then proposed nothing (or something the executor could not do). The list itself is in
# `kaval_agent.fixes` and is added to the END of the message, after the incident (`user_message`),
# because a 1B model follows what it read last; this sentence only tells it to look there.
DEFAULT_SYSTEM_PROMPT = (
    BASE_SYSTEM_PROMPT + " The message ends with the list of fixes you may propose, and the exact "
    "target to use. Follow it."
)


def user_message(context: Context) -> str:
    """What the model reads for one incident: the context, then the fixes it may propose. Shared
    by the local model (`diagnose`) and the Bedrock escalation (`escalate`), so both are told the
    same."""
    section = fixes.render_for_prompt(context.fix_target, context.fix_cause)
    return context.render() + "\n\n" + section


class DiagnosisError(RuntimeError):
    """Ollama failed, or two attempts both returned a reply that didn't validate."""


class HealthIncidentError(RuntimeError):
    """The incident is for a person (Kaval's own health, a full disk, a slow service), so no
    proposal is written. Named for the first such case (KAV-78); the rule is wider (KAV-81)."""


@dataclass(frozen=True)
class ModelUsage:
    tokens_in: int
    tokens_out: int


def diagnose(
    context: Context, *,
    base_url: str | None = None, model: str | None = None, timeout: float = 60.0,
    client: httpx.Client | None = None, max_attempts: int = 2,
    system_prompt: str | None = None, diagnosis_model: type[Diagnosis] | None = None,
    user_message_override: str | None = None, narrow_to_incident: bool = True,
) -> tuple[Diagnosis, ModelUsage]:
    """Ask the model to diagnose `context`. Pure with respect to the database — takes a
    already-built `Context`, returns a validated `Diagnosis`; writing it is `write_proposal`'s
    job. `client` is the same test-injection point `embeddings.embed` uses: an `httpx.Client`
    over `httpx.MockTransport`, so this is testable without a real Ollama.

    `system_prompt`, `diagnosis_model` and `user_message_override` default to the current ones.
    `narrow_to_incident=False` sends the plain schema. Only the eval harness passes others: it
    keeps the pre-KAV-84 prompt as a frozen baseline so a before-and-after is measured in one
    run, on the same database state (`evals/baseline.py`)."""
    base_url = base_url or os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434")
    model = model or os.environ.get("LOCAL_MODEL", "")
    if not model:
        raise DiagnosisError("no model given and LOCAL_MODEL is not set")

    user_message_text = user_message_override or user_message(context)
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt or DEFAULT_SYSTEM_PROMPT},
        {"role": "user", "content": user_message_text},
    ]
    model_cls = diagnosis_model or Diagnosis
    schema = model_cls.model_json_schema()
    cause = context.fix_cause if narrow_to_incident else None
    if narrow_to_incident:
        schema = fixes.narrow_schema(schema, cause, context.fix_target)
    owns_client = client is None
    http = client or httpx.Client()
    tokens_in = tokens_out = 0
    last_error: ValidationError | None = None
    last_problem: str | None = None
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
                diagnosis = model_cls.model_validate_json(content)
            except ValidationError as exc:
                last_error = exc
                last_problem = None
                messages.append({"role": "assistant", "content": content})
                messages.append({
                    "role": "user",
                    "content": "That did not match the required schema:\n"
                                f"{exc}\n"
                                "Reply again with corrected JSON only, matching the schema. "
                                "No prose, no markdown fences.",
                })
                continue
            problem = fixes.violations(diagnosis, cause, context.fix_target)
            if problem is not None:
                last_error = None
                last_problem = problem
                messages.append({"role": "assistant", "content": content})
                messages.append({
                    "role": "user",
                    "content": f"That reply broke a rule: {problem}.\n"
                                "Reply again with corrected JSON only, matching the schema. "
                                "No prose, no markdown fences.",
                })
                continue
            return diagnosis, ModelUsage(tokens_in=tokens_in, tokens_out=tokens_out)
    finally:
        if owns_client:
            http.close()
    raise DiagnosisError(
        f"{model} did not return a valid diagnosis after {max_attempts} attempt(s): "
        f"{last_problem or last_error}"
    ) from last_error


def write_proposal(
    session: Session, incident: Incident, diagnosis: Diagnosis, usage: ModelUsage, *, model: str,
    cost_usd: Decimal = Decimal("0"),
) -> Proposal:
    """Persist a validated diagnosis as one `proposal` row plus one `action` row per proposed
    action. Nothing here is called unless `diagnose()` (or `escalate()`) already returned
    successfully — there is deliberately no path that writes an unvalidated diagnosis.

    **Refuses the incidents that are for a person** (`kaval_shared.health`): Kaval's own health
    (KAV-78), and a full disk or a slow service (KAV-81, ADR-0047). The right outcome is a person,
    never a proposed fix. This is the one place every proposal passes through, so it is the place
    the rule is enforced. The policy refuses again from the cause (`never`), and so does the
    executor, so a proposal filed by some other route still cannot run.

    **Stores a confidence the evidence supports** (KAV-88, ADR-0056): the model's own number is
    only a ceiling, lowered by `kaval_agent.evidence` when the incident holds one observation or
    one kind of observation. The policy is asked about the number that is stored.

    `cost_usd` defaults to 0: self-hosted local inference has no metered cost. A Bedrock
    escalation (KAV-44, ADR-0019) passes its real, measured cost instead —
    `kaval_agent.escalate.estimate_cost_usd` — never a guess made in advance."""
    if health.is_people_only_fingerprint(incident.fingerprint):
        raise HealthIncidentError(
            f"{incident.fingerprint} is for a person, not a proposal (no automated fix exists)"
        )
    confidence = evidence.limit(
        diagnosis.confidence, incident.fingerprint, incident.signals,
    ).confidence
    proposal = Proposal(
        incident_id=incident.id,
        summary=diagnosis.summary,
        root_cause=diagnosis.root_cause,
        confidence=confidence,
        risk=RiskLevel(diagnosis.risk),
        model=model,
        tokens_in=usage.tokens_in,
        tokens_out=usage.tokens_out,
        cost_usd=cost_usd,
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
            policy_class=policy.classify(
                a.type, blast_radius, a.reversible, confidence,
                cause=health.cause_of_fingerprint(incident.fingerprint),
            ),
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
    parser.add_argument("--escalate", action="store_true",
                        help="allow escalation to Bedrock (KAV-44, ADR-0019) when the local "
                             "model is unconfident or the context has no evidence. Incurs a "
                             "small, real Bedrock cost and requires BEDROCK_MODEL_ID plus AWS "
                             "credentials (AWS_PROFILE=kaval). Without this flag, the local "
                             "model is the only path, same as before KAV-44.")
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
        if health.is_people_only_fingerprint(incident.fingerprint):
            sys.exit(f"{incident.fingerprint} is for a person: no diagnosis is asked for and no "
                     "fix proposed (see docs/runbooks/, the runbook for this cause)")
        changes = None
        if args.with_changes:
            from kaval_agent import recent_changes as rc
            _cause, _domain, subject = incident.fingerprint.split(":", 2)
            changes = rc.for_workload(subject.rsplit("/", 1)[-1])
        context = build_context(session, incident, changes=changes)

        cost = Decimal("0")
        if args.escalate:
            from kaval_agent.escalate import diagnose_with_escalation

            try:
                result = diagnose_with_escalation(
                    context, local_model=os.environ.get("LOCAL_MODEL", "")
                )
            except DiagnosisError as exc:
                sys.exit(f"diagnosis failed: {exc}")
            diagnosis, usage, model, cost = (
                result.diagnosis, result.usage, result.model, result.cost_usd
            )
            if result.escalated:
                print(f"(escalated to Bedrock: {model}, ~${cost})")
        else:
            try:
                diagnosis, usage = diagnose(context)
            except DiagnosisError as exc:
                sys.exit(f"diagnosis failed: {exc}")
            model = os.environ.get("LOCAL_MODEL", "")

        print(diagnosis.model_dump_json(indent=2))
        if args.dry_run:
            print("(--dry-run: nothing written)")
            return 0

        proposal = write_proposal(
            session, incident, diagnosis, usage, model=model, cost_usd=cost
        )
        classes = ", ".join(a.policy_class.value for a in proposal.actions) or "no actions"
        print(f"\nwrote proposal {proposal.id} with {len(diagnosis.actions)} action(s): {classes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
