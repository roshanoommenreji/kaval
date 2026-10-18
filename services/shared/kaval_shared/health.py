"""Which problems are for a person and never for the executor (KAV-78, KAV-81).

Two kinds of incident have no automated fix, and both rules below are enforced in code rather than
left to a model's judgement:

- **Kaval's own health** (KAV-78): a cause whose name starts `kaval_` (`kaval_loop_stalled`,
  `kaval_prometheus_unreachable`) means Kaval itself has stopped doing its job. The executor's
  cluster permissions cover only its demo namespace, so it could not restart Kaval's own pods
  anyway.
- **Disk full and slowness** (KAV-81, ADR-0047): `disk_pressure` and `latency_high`. A restart
  does not free a disk and does not speed up a slow service, and the real fixes (grow a volume,
  find what is filling it, find what is slow) are a person's judgement. A confident wrong action
  here costs more than a page.

For both, the same two things follow:

- **No fix is proposed.** `kaval_agent.diagnose.write_proposal` refuses these incidents, the policy
  marks any action attached to one `never`, and the executor's second policy check refuses it
  again. The model is never asked to guess.
- **A person is told directly.** `kaval_gateway.health_alerts` posts them to Slack straight from
  the signals, without waiting for the correlator, because the correlator may be the thing that
  stopped.

The list below is repeated in `policy/policy.rego` (the executor cannot import Python into the
policy); `services/agent/tests/test_policy.py` fails if the two ever differ.

Tiny and dependency-free on purpose: the agent, gateway, executor and tests all import it.
"""

from __future__ import annotations

PREFIX = "kaval_"
# The waste scan's findings (KAV-94, ADR-0062): signal kind -> cause. They are for a person until
# removal exists (KAV-95), which will have to take its own kinds out of this list on purpose. A
# disk attached to nothing is not here: it keeps its older path (`idle_volume`, explained but never
# fixed by the model, and measured by the golden evals).
WASTE_SIGNALS = {
    "address_unattached": "idle_address",
    "snapshot_past_retention": "snapshot_past_retention",
    "server_overstay": "server_overstay",
    "resource_unlabelled": "resource_unlabelled",
}
# Causes that are not Kaval's own health but still have no automated fix. Same list in policy.rego.
PEOPLE_ONLY_CAUSES = frozenset({"disk_pressure", "latency_high", *WASTE_SIGNALS.values()})


def is_health_cause(cause: str) -> bool:
    return cause.startswith(PREFIX)


def is_health_fingerprint(fingerprint: str) -> bool:
    """`kaval_loop_stalled:k8s:kaval-staging/executor` -> True (`cause:domain:subject`)."""
    return is_health_cause(cause_of_fingerprint(fingerprint))


def cause_of_fingerprint(fingerprint: str) -> str:
    return fingerprint.split(":", 1)[0]


def is_people_only_cause(cause: str) -> bool:
    """True for Kaval's own health and for the causes with no automated fix."""
    return is_health_cause(cause) or cause in PEOPLE_ONLY_CAUSES


def is_people_only_fingerprint(fingerprint: str) -> bool:
    return is_people_only_cause(cause_of_fingerprint(fingerprint))


# The signal kinds that become those causes (KAV-81). One table for the correlator (which names
# the incident) and the Slack alert (which finds the signals), so neither can drift from the other.
PEOPLE_ONLY_SIGNALS = {
    "disk_near_full": "disk_pressure",
    "service_slow": "latency_high",
    **WASTE_SIGNALS,
}
