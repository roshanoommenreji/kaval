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
* **several, but all of one kind** (three "restarting" events and never why): at most 0.7. Unless
  that one kind is the **direct evidence of the incident's own cause** (`DIRECT_EVIDENCE`): the
  kernel's own "killed for using too much memory" report is the cause, not a hint about it, so a
  real memory incident (17 of those events and nothing else, seen on staging 2026-10-16) keeps the
  model's number. One such observation alone is still only a hint: at most 0.5.
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

# The cause the correlator names (the first part of the fingerprint) and the one kind of signal
# whose own report states it. Found live on staging 2026-10-16 (KAV-88): a real out-of-memory
# incident holds only `pod_oom_killed`, so "one kind" cannot mean "thin" for it. Anything not
# listed here (a crash loop is the restarts, never the reason) keeps the 0.7 limit.
DIRECT_EVIDENCE = {"oom_killed": "pod_oom_killed", "exec_format": "container_exited"}


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
    elif len(kinds) == 1 and kinds == {DIRECT_EVIDENCE.get(fingerprint.split(":", 1)[0], "")}:
        return Limit(stated, stated, "")
    elif len(kinds) == 1:
        cap, reason = ONE_KIND_CAP, f"{len(signals)} observations, but all of one kind"
    else:
        return Limit(stated, stated, "")
    confidence = min(stated, cap)
    if confidence == stated:
        return Limit(stated, stated, "")
    return Limit(confidence, stated, f"{reason}: stated {stated:.2f}, stored {confidence:.2f}")
