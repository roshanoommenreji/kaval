"""The diagnosis prompt exactly as it was before KAV-84 (ADR-0054), frozen, so a before-and-after is
one command on one database instead of two runs on a database that has changed in between.

Why not "git stash and run again": every eval run writes real incident rows, and a later run reads
the earlier ones back as history in its context. A run made after another therefore does not see
what the first one saw. Running the old and the new prompt in the same pass, case by case,
alternating, puts both on the same footing (`evals/run.py --prompt before|after|both`).

This file is a copy on purpose. It must NOT import `kaval_agent.diagnose.BASE_SYSTEM_PROMPT` or
`kaval_agent.fixes`: the day someone edits those, the baseline would move with them and the
comparison would quietly measure nothing. Never edit the text below; to measure a newer prompt
against an older one, add a new frozen copy beside it.
"""

from __future__ import annotations

from kaval_agent.schema import Diagnosis, DiagnosisAction
from pydantic import Field

BASELINE_SYSTEM_PROMPT = (
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

# The schema's own description of `action.type`, as it read before KAV-84. It named
# `scale_deployment`, which the executor has never been able to do.
BASELINE_TYPE_DESCRIPTION = (
    "e.g. restart_pod, increase_memory_limit, rollback_deployment, scale_deployment"
)


class BaselineAction(DiagnosisAction):
    """An action as the model was told about it before KAV-84: the old wording of `type`."""

    type: str = Field(min_length=1, description=BASELINE_TYPE_DESCRIPTION)


class BaselineDiagnosis(Diagnosis):
    """`Diagnosis` as it was: `actions` has a default, so it is OPTIONAL in the JSON Schema the
    model decodes against, and a reply that leaves it out is still valid (an empty list). That was
    the main reason the model never proposed anything (ADR-0054). Both the schema sent and the
    validation of the reply use this class for the `before` arm."""

    actions: list[BaselineAction] = Field(default_factory=list, max_length=5)  # type: ignore[assignment]
