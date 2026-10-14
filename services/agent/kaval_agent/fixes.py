"""The fixes Kaval can actually carry out, and which incidents each one answers (KAV-84, ADR-0054).

Until this story the model was never given the list. It named the cause correctly and then
proposed nothing (the oom game day, 3 of 3), or it invented a type the executor has no handler for
and the action was skipped. The list lives here, once, and three things read it:

- the message the model reads (`render_for_prompt`, used by `kaval_agent.diagnose`), which shows
  only the fixes that answer THIS incident's cause;
- the schema's description of `action.type` (`kaval_agent.schema`);
- a test that fails if this list and `kaval_executor.k8s.SUPPORTED_ACTIONS` ever differ
  (`tests/test_fixes.py`), so adding an executor action without teaching the model, or the other
  way round, cannot pass CI.

Why the message shows one fix and the reply format allows only that one. A 1B model given all
three fixes and asked to choose proposed all three, or none, or an invented fourth, and changed its
mind between identical runs; told only the right one, it still declined most of the time. With the
reply format itself narrowed to the incident (`narrow_schema`) it was right 15 times in 15 (Lab 51).
The cause is not the model's to guess: the correlator names it from the signals
(`cause:domain:subject` in the incident's fingerprint), so the lookup below is deterministic and a
sentence planted in a log line cannot change it. The model writes the diagnosis; the fix follows
from the cause; a person approves it and policy still gates it (ADR-0054).

This module is pure text and tuples: no database, no httpx, no executor import, so the schema can
use it. It decides nothing about what runs. Policy (`policy/policy.rego`) and the executor's own
re-check still decide that, and an action off this list is still validated, classified and refused
the way it was before.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel


@dataclass(frozen=True)
class Fix:
    type: str
    does: str
    use_when: str
    not_when: str
    blast_radius: str  # a `schema.BlastRadiusValue`
    reversible: bool
    # The incident causes this fix answers: the first part of an incident's fingerprint, as the
    # correlator names it (`kaval_agent.correlate.cause_of`). A cause in none of these has no fix.
    causes: tuple[str, ...]


FIXES: tuple[Fix, ...] = (
    Fix(
        type="increase_memory_limit",
        does="doubles the container's memory limit on its Deployment (up to a fixed ceiling)",
        use_when="a container is killed for running out of memory (OOMKilled, exit code 137) "
                 "and comes back and dies again",
        not_when="the pod is failing for any other reason",
        blast_radius="deployment",
        reversible=True,
        causes=("oom_killed", "memory_pressure"),
    ),
    Fix(
        type="rollback_deployment",
        does="puts the Deployment back on the version it ran before the current one",
        use_when="a pod will not start or keeps crashing on start: an 'exec format error' (the "
                 "image was built for the wrong CPU architecture) or any crash since the latest "
                 "release",
        not_when="the problem is not a release (a memory kill, a cost spike, a full disk)",
        blast_radius="deployment",
        reversible=True,
        causes=("exec_format", "crashloop", "container_error"),
    ),
    Fix(
        type="restart_pod",
        does="deletes one pod so its controller creates a fresh one",
        use_when="one pod is stuck or unhealthy for no clear reason",
        not_when="the pod is out of memory or on a bad release: it comes back exactly the same",
        blast_radius="pod",
        reversible=True,
        causes=("crashloop", "container_error"),
    ),
)

FIX_TYPES: frozenset[str] = frozenset(f.type for f in FIXES)


def fixes_for(cause: str | None) -> tuple[Fix, ...]:
    """The fixes that answer `cause`; every fix when the cause is not known."""
    if cause is None:
        return FIXES
    return tuple(f for f in FIXES if cause in f.causes)


def narrow_schema(schema: dict[str, Any], cause: str | None, target: str | None) -> dict[str, Any]:
    """`Diagnosis`'s JSON Schema narrowed to ONE incident, for the constrained decoding Ollama does.

    - a cause with a supported fix: exactly one action, whose `type` can only be a fix that answers
      the cause and whose `target` can only be the incident's own workload;
    - a cause with none (a cost spike, a full disk, Kaval's own health, anything the correlator
      names that no fix answers): no action at all;
    - an unknown cause (None): the schema is returned unchanged.

    A copy is returned; the shared schema is never touched. The reply is checked again afterwards
    (`violations`) because this narrowing is a property of this Ollama version, not a promise."""
    if cause is None:
        return schema
    narrowed = copy.deepcopy(schema)
    actions = narrowed["properties"]["actions"]
    relevant = fixes_for(cause)
    if not relevant:
        actions["maxItems"] = 0
        return narrowed
    actions["minItems"] = actions["maxItems"] = 1
    props = narrowed["$defs"]["DiagnosisAction"]["properties"]
    props["type"]["enum"] = [f.type for f in relevant]
    if target:
        props["target"]["enum"] = [target]
    return narrowed


def violations(diagnosis: BaseModel, cause: str | None, target: str | None) -> str | None:
    """Why a validated reply breaks the narrowing above, or None. Same rules, checked on the parsed
    reply, so a model or server that ignored the narrowed schema still cannot get through."""
    if cause is None:
        return None
    actions = getattr(diagnosis, "actions", [])
    relevant = fixes_for(cause)
    if not relevant:
        return (f"Kaval has no fix for a {cause} incident; actions must be an empty list"
                if actions else None)
    if len(actions) != 1:
        return f"actions must hold exactly one action for a {cause} incident, not {len(actions)}"
    action = actions[0]
    allowed = [f.type for f in relevant]
    if action.type not in allowed:
        return f"action type must be one of {allowed}, not {action.type!r}"
    if target and action.target != target:
        return f"action target must be exactly {target!r}, not {action.target!r}"
    return None


def type_hint() -> str:
    """The schema's description of `action.type`: the real names, nothing else."""
    return "one of: " + ", ".join(f.type for f in FIXES)


def render_for_prompt(target: str | None = None, cause: str | None = None) -> str:
    """The section added to the END of the message the model reads, after the incident (KAV-84).

    Plain and literal on purpose: it is read by a 1B model. It goes last because that is the part
    such a model weighs most (the same words in the system prompt were followed in 1 of 5 tries,
    at the end in 5 of 5: ADR-0054, Lab 51). `target` is the incident's own `namespace/name`,
    handed over because the model otherwise invents one or copies it from an example. `cause`
    narrows the list to the fixes that answer this kind of incident."""
    relevant = fixes_for(cause)
    if not relevant:
        return (
            f"NO FIX AVAILABLE. Kaval has no fix it can carry out for this kind of incident "
            f"({cause}). Return an empty actions list: a person will handle it. Do not write any "
            "action."
        )
    kind = f" FOR THIS KIND OF INCIDENT ({cause})" if cause else ""
    lines = [
        f"FIXES YOU MAY PROPOSE{kind}. These are the only actions Kaval can carry out. Use the "
        "exact type name. Never write any other action type.",
    ]
    for f in relevant:
        lines.append(
            f"- {f.type}: {f.does}. Use when {f.use_when}. Do not use when {f.not_when}. "
            f"blast_radius {f.blast_radius}, reversible {str(f.reversible).lower()}."
        )
    if cause:
        lines.append("Put exactly one action in actions.")
    else:
        lines.append(
            "Pick the one fix that matches the cause, and do not list the others. If none of "
            "these fixes fits the incident, return an empty actions list: a person will handle it."
        )
    if target:
        lines.append(f"For any action, the target is exactly: {target}. params stays empty.")
    else:
        lines.append("For any action, target is the workload named in the incident. params "
                     "stays empty.")
    return "\n".join(lines)
