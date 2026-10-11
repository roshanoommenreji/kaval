"""Which problems are Kaval's own, and so are for a person and never for the executor (KAV-78).

A cause whose name starts `kaval_` (`kaval_loop_stalled`, `kaval_prometheus_unreachable`) means
Kaval itself has stopped doing its job. Two rules follow from that, and both are enforced in code
rather than left to a model's judgement:

- **No fix is proposed.** `kaval_agent.diagnose.write_proposal` refuses these incidents. The
  executor's cluster permissions cover only its demo namespace, so it could not restart Kaval's
  own pods anyway; refusing at the source means there is no pending approval that looks like a
  fix but can never work, and the model is never asked to guess at its own failure.
- **A person is told directly.** `kaval_gateway.health_alerts` posts them to Slack straight from
  the signals, without waiting for the correlator, because the correlator may be the thing that
  stopped.

Tiny and dependency-free on purpose: the agent, the gateway and the tests all import it.
"""

from __future__ import annotations

PREFIX = "kaval_"


def is_health_cause(cause: str) -> bool:
    return cause.startswith(PREFIX)


def is_health_fingerprint(fingerprint: str) -> bool:
    """`kaval_loop_stalled:k8s:kaval-staging/executor` -> True (`cause:domain:subject`)."""
    return is_health_cause(fingerprint.split(":", 1)[0])
