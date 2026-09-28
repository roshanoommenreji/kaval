"""The Pydantic schema an LLM diagnosis must satisfy before it can become a `proposal` row
(KAV-41, ADR-0016).

Kept separate from `diagnose.py` so anything that needs the shape without a live Ollama —
the eval harness later in Phase 2, a test, a future preview endpoint on the gateway — can
import it without pulling in httpx.

`extra="forbid"` on both models: a field the model invents (or a field it drops that a
default would silently fill) is worth failing loudly on, not tolerating. This is the schema
docs/learn/phase-2-the-agent-loop.md means by "schema validation that rejects rather than
parses hopefully".
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Mirrors kaval_shared.models.BlastRadius / RiskLevel exactly. Not imported from there:
# kaval_shared.models is a SQLAlchemy module (it would pull the ORM and Postgres driver
# into anything that only wants the schema), and Literal gives Ollama's structured-output
# support a plain enum in the generated JSON Schema either way. diagnose.py is the one place
# that has to keep the two in sync, and it does so by construction: it converts every
# Diagnosis field into the matching kaval_shared enum, so a real drift fails there, at write
# time, not silently.
BlastRadiusValue = Literal["pod", "deployment", "namespace", "node", "account"]
RiskValue = Literal["low", "medium", "high"]


class DiagnosisAction(BaseModel):
    """One action the model proposes. There is no `policy_class` field here on purpose: the
    model doesn't get an opinion on its own blast radius. `diagnose.write_proposal` sets
    every action's real `Action.policy_class` from these fields (today: hardcoded to `ask`,
    since the classifier that should do it is the next story on the roadmap) — never from
    anything the model says about itself."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1, description="e.g. restart_pod, scale_deployment")
    target: str = Field(min_length=1, description="the concrete resource, e.g. "
                         "kaval-demo/checkout")
    params: dict[str, object] = Field(default_factory=dict)
    reversible: bool
    blast_radius: BlastRadiusValue


class Diagnosis(BaseModel):
    """What the model must return for one incident. There is no partial acceptance: a reply
    that doesn't validate is not "mostly a proposal", it's not a proposal — see diagnose.py's
    module docstring."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=280, description="one line, phone-readable")
    root_cause: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    risk: RiskValue
    # A cap, not just a floor: an ungrounded model asked for "actions" can free-associate a
    # long list. Five is already more than a human would want to triage for one incident.
    actions: list[DiagnosisAction] = Field(default_factory=list, max_length=5)
