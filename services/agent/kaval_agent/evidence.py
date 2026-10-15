"""The stored confidence can never be higher than the evidence allows (KAV-88, ADR-0056).

The model's own "how sure am I" does not follow how much it was shown. Measured twice (Lab 13,
Lab 51): with a single raw observation and no message it still said 0.75 to 0.95, the same as
with five. Asking it to be humbler in the prompt did not move it (ADR-0018), and sampling it
several times to see whether it agrees with itself would cost three to five minutes of the 4 GB
node's processor per incident. So the number is limited by a rule we can read: the model states a
ceiling, and this module can only lower it.

The rule is deliberately small, because a rule nobody can explain is no better than the number it
replaces. It applies to an incident about a Kubernetes workload, the only kind a fix can be aimed
at, and counts what the incident actually holds:

* **one observation**: a hint, not a diagnosis. At most 0.5.
* **several, but all of one kind** (three "restarting" events and never why): at most 0.7.
* **two or more kinds** (a memory reading and the kill itself): the model's number stands.

An incident that is not about a workload (a cost spike, an unused disk) is one measurement by
nature and has no fix to be overconfident about, so it is left alone.

Used in `diagnose.write_proposal`, the one place every proposal passes through, so the laptop, the
cluster worker, the Bedrock path and the evals all get the same number. It only ever lowers: a
lower stored confidence can make `policy` more careful (an `auto` needs confidence above a
threshold) and never less.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from kaval_shared.models import Signal

from kaval_agent.context import fix_target_of

ONE_OBSERVATION_CAP = 0.5
ONE_KIND_CAP = 0.7


@dataclass(frozen=True)
class Limit:
    confidence: float  # what is stored: never above `stated`
    stated: float      # what the model said
    reason: str        # empty when nothing was changed

    @property
    def limited(self) -> bool:
        return self.confidence < self.stated


def limit(stated: float, fingerprint: str, signals: Sequence[Signal]) -> Limit:
    """The confidence to store for an incident with this fingerprint and these signals."""
    if fix_target_of(fingerprint) is None:
        return Limit(stated, stated, "")
    kinds = {s.kind for s in signals}
    if len(signals) <= 1:
        cap, reason = ONE_OBSERVATION_CAP, "only one observation behind this diagnosis"
    elif len(kinds) == 1:
        cap, reason = ONE_KIND_CAP, f"{len(signals)} observations, but all of one kind"
    else:
        return Limit(stated, stated, "")
    confidence = min(stated, cap)
    if confidence == stated:
        return Limit(stated, stated, "")
    return Limit(confidence, stated, f"{reason}: stated {stated:.2f}, stored {confidence:.2f}")
